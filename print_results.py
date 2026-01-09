import json
import os
import sys
import argparse
from collections import defaultdict

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
    final_metrics = {
        'model_name': model_name
    }

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
    print(final_metrics)
    return final_metrics

def main():
    parser = argparse.ArgumentParser(description='Analyze Test@k results and generate formatted table.')
    parser.add_argument('--k_min', type=int, default=1, help='Minimum k value (default: 1)')
    parser.add_argument('--k_max', type=int, default=5, help='Maximum k value (default: 5)')
    parser.add_argument("--benchmark_name", type=str, default='ULT')
    
    args = parser.parse_args()
    

    k_values = [1,2,5]
    # print(f"Analyzing with k values: {k_values}")
    
    with open('models.txt', 'r', encoding='utf-8') as f:
        model_list = f.read().splitlines()
    models = [model.split('/')[-1] for model in model_list]
    
    results = {}

    # with open(f"data/testbench/pytest_results/{model_name}.json", "r") as f:
        # global_results = [json.loads(line) for line in f.readlines()]
        # global_results = json.load(f)

    for model_name in models:
        input_file = f'data/{args.benchmark_name}/pytest_results/{model_name}.json'
        if not os.path.exists(input_file):
            print(f"{model_name} does not exists")
            continue

        try:
            result = analyze_test_at_k_results(input_file, model_name, k_values, global_results=None)
            results[model_name] = result
            # print(f"Successfully processed {input_file}")
        except Exception as e:
            print(f"Error processing {input_file}: {str(e)}")
    
    # # 打印格式化结果
    # print("\n" + "="*50 + " RESULTS " + "="*50)
    # print_formatted_results(results, k_values)
    
    for model_name in results.keys():
        result_pass = []
        result_line_cov = []
        result_branch_cov = []
        for key, value in results[model_name].items():

            # if key!="model_name":
            #     result.append(round(value,2))
            # else:
            #     result.append(value)
            if key=="model_name":
                result_pass.append(value)
                result_line_cov.append(value)
                result_branch_cov.append(value)
            elif key.startswith("pass@"):
                result_pass.append(round(value, 2))
            elif key.startswith("line_cov@"):
                result_line_cov.append(round(value, 2))
            elif key.startswith("branch_cov@"):
                result_branch_cov.append(round(value, 2))
        # print(f"{model_name}:")

        # print("pass_" + "&".join([str(i) for i in result_pass]) + "\\\\")
        # print("line_cov_" + "&".join([str(i) for i in result_line_cov]) + "\\\\")
        # print("branch_cov_" + "&".join([str(i) for i in result_branch_cov]) + "\\\\")

if __name__ == "__main__":
    main()
