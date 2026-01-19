import json
import os
import sys
import re
import argparse
import subprocess
import wandb
from collections import defaultdict
from tqdm.contrib.concurrent import process_map

def analyze_test_at_k_results(input_file, model_name=None, k_values=None, global_results=None):
    """分析Test@k结果，使用新的计算方式聚合各指标"""
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    # 动态创建聚合指标字典
    aggregated_metrics = {}
    for k in k_values:
        aggregated_metrics[f'pass@{k}'] = {'passed_tests': 0, 'total_tests': 0}
        aggregated_metrics[f'line_cov@{k}'] = {'covered_stmts': 0, 'stmts': 0}
        aggregated_metrics[f'branch_cov@{k}'] = {'covered_branches': 0, 'total_branches': 0}
    
    task_num = 0
    for entry in data:

        try:
            for k in k_values:

                test_k_key = f'test@{k}'
                if k==1 and "coverage_error" in entry["test_at_k"][test_k_key]["result"][1].keys():
                    for i in range(1, k+1):
                        aggregated_metrics[f"pass@{i}"]["passed_tests"] += entry["test_at_k"][f'test@{i}']["result"][0]["test_counts"]["passed_tests"]
                    break

                if k>=2 and "coverage_error" in entry["test_at_k"][test_k_key]["result"][1].keys():
                    entry["test_at_k"][test_k_key]["result"][1] = entry["test_at_k"][f'test@{k-1}']["result"][1]
                if k>=2 and k-1 in k_values and "coverage_error" in entry["test_at_k"][f'test@{k-1}']["result"][1].keys():
                    aggregated_metrics[f"pass@{k}"]["passed_tests"] += entry["test_at_k"][test_k_key]["result"][0]["test_counts"]["passed_tests"]
                    continue
                # 检查数据中是否存在对应的k值
                if test_k_key in entry.get("test_at_k", {}):
                    aggregated_metrics[f"pass@{k}"]["passed_tests"] += entry["test_at_k"][test_k_key]["result"][0]["test_counts"]["passed_tests"]

                    aggregated_metrics[f"line_cov@{k}"]["covered_stmts"] += entry["test_at_k"][test_k_key]["result"][1]["stmts"] - entry["test_at_k"][test_k_key]["result"][1]["miss_stmts"]
                    aggregated_metrics[f"line_cov@{k}"]["stmts"] += entry["test_at_k"][test_k_key]["result"][1]["stmts"]

                    aggregated_metrics[f"branch_cov@{k}"]["covered_branches"] += entry["test_at_k"][test_k_key]["result"][1]["covered_branches"]
                    aggregated_metrics[f"branch_cov@{k}"]["total_branches"] += entry["test_at_k"][test_k_key]["result"][1]["total_branches"]
        except Exception as e:
            print(e)
            pass
    
    # 计算最终指标
    final_metrics = {}

    # 计算通过率
    for k in k_values:
        metrics_key = f'pass@{k}'
        passed = aggregated_metrics[metrics_key]['passed_tests']
        total = 3909 * k

        final_metrics[metrics_key] = 100 * passed / total if total > 0 else 0

    
    # # 计算行覆盖率
    # for k in k_values:
    #     metrics_key = f'line_cov@{k}'
    #     miss = aggregated_metrics[metrics_key]['covered_stmts']
    #     total = sum([global_results[key]["stmts"] for key in global_results.keys()])
    #     final_metrics[metrics_key] = (100 * miss / total if total > 0 else 0)
    for k in k_values:
        metrics_key = f'line_cov@{k}'
        covered = aggregated_metrics[metrics_key]['covered_stmts']
        total = aggregated_metrics[metrics_key]['stmts']
        final_metrics[metrics_key] = (100 * covered / total if total > 0 else 0)
    
    # # 计算分支覆盖率
    # for k in k_values:
    #     metrics_key = f'branch_cov@{k}'
    #     covered = aggregated_metrics[metrics_key]['covered_branches']
    #     total = sum([global_results[key]["total_branches"] for key in global_results.keys()])
    #     final_metrics[metrics_key] = (100 * covered / total if total > 0 else 0)
    for k in k_values:
        metrics_key = f'branch_cov@{k}'
        covered = aggregated_metrics[metrics_key]['covered_branches']
        total = aggregated_metrics[metrics_key]['total_branches']
        final_metrics[metrics_key] = (100 * covered / total if total > 0 else 0)
    return final_metrics

def mutation_statistic_wrapper(benchmark_name, model_name, num_test_cases, task):
    working_dir = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/{task}'

    statistic_info = {
        "task": task,
        "complete_rate": 0.0,
        "surviving_mutants_rate": 0.0,
        "total_jobs_number": 0,
        "completed_jobs_number": 0,
        "surviving_mutants_number": 0
    }

    try:
        response = subprocess.run(['cr-report', f'cosmic-ray.sqlite', '--show-pending'], cwd=working_dir, check=True, capture_output=True, text=True)
    except Exception as e:
        print(f'[-] Error @ [{working_dir}]: {e}')
        return statistic_info

    total_jobs_match = re.search(r"total jobs:\s*(\d+)", response.stdout)
    completed_jobs_match = re.search(r"complete:\s*(\d+)\s*\(", response.stdout)
    surviving_mutants_match = re.search(r"surviving mutants:\s*(\d+)\s*\(", response.stdout)

    if total_jobs_match:
        total_jobs_number = int(total_jobs_match.group(1))
        statistic_info["total_jobs_number"] = total_jobs_number

    if completed_jobs_match:
        completed_jobs_number = int(completed_jobs_match.group(1))
        statistic_info["completed_jobs_number"] = completed_jobs_number
        
    if surviving_mutants_match:
        surviving_mutants_number = int(surviving_mutants_match.group(1))
        statistic_info["surviving_mutants_number"] = surviving_mutants_number
    
    statistic_info['complete_rate'] = statistic_info['completed_jobs_number'] / statistic_info["total_jobs_number"] if statistic_info["total_jobs_number"] > 0 else 0
    statistic_info['surviving_mutants_rate'] = (statistic_info['surviving_mutants_number'] / statistic_info['completed_jobs_number']) if statistic_info['completed_jobs_number'] > 0 else 0

    return statistic_info


def mutation_statistic(benchmark_name, model_name, num_test_cases, baseline_test_cases=5):
    correct_tasks = list()
    correct_tasks_path = f'data/{benchmark_name}/correct_tasks_tc_{baseline_test_cases}_{model_name}'
    
    with open(correct_tasks_path, 'r') as f:
        for line in f.readlines():  
            correct_tasks.append(line.strip())
    
    # print(f'[+] ✅ Correct Tasks: {len(correct_tasks)}')
    final_tasks = correct_tasks
    
    surviving_mutants_rate = 0.0

    statistics = process_map(mutation_statistic_wrapper, [benchmark_name]*len(final_tasks), [model_name]*len(final_tasks), [num_test_cases]*len(final_tasks), final_tasks, desc=f"[+] 🔄 Running mutation ({num_test_cases} test cases) statistics...", chunksize=1, leave=False)
    for statistic in statistics:
        # print(f"[+] {statistic}")
        surviving_mutants_rate += statistic["surviving_mutants_rate"]
    
    surviving_mutants_rate = (surviving_mutants_rate / len(correct_tasks)) if len(correct_tasks) > 0 else 0.0

    return (1.0 - surviving_mutants_rate) * 100

def main():
    parser = argparse.ArgumentParser(description='Analyze Test@k results and generate formatted table.')
    parser.add_argument('--k_min', type=int, default=1, help='Minimum k value (default: 1)')
    parser.add_argument('--k_max', type=int, default=5, help='Maximum k value (default: 5)')
    parser.add_argument("--benchmark_name", type=str, default='ULT')
    
    args = parser.parse_args()
    

    k_values = [1,2,5]
    # print(f"Analyzing with k values: {k_values}")

    wandb.init(project='UnLeankedTestBench', name=args.benchmark_name)
    columns = ["Model"]
    for k in k_values: columns.append(f"pass@{k}")
    for k in k_values: columns.append(f"line_cov@{k}")
    for k in k_values: columns.append(f"branch_cov@{k}")
    for k in k_values: columns.append(f"mut@{k}")
    wandb_table = wandb.Table(columns=columns)
    
    with open('models.txt', 'r', encoding='utf-8') as f:
        model_list = f.read().splitlines()
    models = [model.split('/')[-1] for model in model_list]

    for model_name in models:
        row_data_dict = {"Model": model_name}

        # --- Pass@k LCov@k BCov@k ---
        input_file = f'data/{args.benchmark_name}/pytest_results/{model_name}.json'
        if not os.path.exists(input_file):
            print(f"{model_name} does not exists")
            continue

        try:
            result = analyze_test_at_k_results(input_file, model_name, k_values, global_results=None)
            row_data_dict.update(result)
        except Exception as e:
            print(f"Error processing {input_file}: {str(e)}")

        # --- Mut@k ---
        try:
            for k in k_values:
                score = mutation_statistic(args.benchmark_name, model_name, k)
                row_data_dict[f"mut@{k}"] = score
        except Exception as e:
            print(f"Error computing Mut@k: {str(e)}")

        # --- Log ---
        print(row_data_dict)
        row_values = []
        for col in columns:
            val = row_data_dict.get(col, 0.0)
            if isinstance(val, (int, float)):
                row_values.append(round(val, 2))
            else:
                row_values.append(val)
        wandb_table.add_data(*row_values)

    wandb.log({f"ULT_Leaderboard": wandb_table})
    wandb.finish()

if __name__ == "__main__":
    main()
