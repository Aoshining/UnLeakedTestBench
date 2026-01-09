# coding: utf-8

# Author: Du Mingzhe (mingzhe@nus.edu.sg)
# Date: 2025-05-27

import re
import os
import json
import string
import random
import shutil
import tempfile
import datetime
import argparse
import subprocess
from tqdm import tqdm
from collections import defaultdict
from tqdm.contrib.concurrent import process_map

toml_template = """
[cosmic-ray]
module-path = "mod.py"
timeout = {timeout}
excluded-modules = []
test-command = "pytest test.py"

[cosmic-ray.distributor]
name = "local"
"""

code_import = """
import os
import re
import math
import numpy
import pandas
import pytest
import random
import string
import warnings
import datetime
import traceback
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Union, Tuple, Set, FrozenSet, Sequence, Iterable, Generator, Callable
"""

def rename_test_functions(test_code):
    test_counter = 0
    new_lines = list()
    pattern = re.compile(r"(\s*def\s+)(test_)(.*)")
    test_code_lines = test_code.split('\n')
    
    for line in test_code_lines:
        match = pattern.match(line)
        if match:
            test_counter += 1
            new_lines.append(f"{match.group(1)}test_{test_counter}_{match.group(3)}")
        else:
            new_lines.append(line)
    
    return '\n'.join(new_lines)

def parse_pytest_output(output: str) -> dict:
    """
    Parses the stdout of a `pytest --cov` run to extract key metrics.

    Args:
        output: The string output from the pytest command.

    Returns:
        A dictionary containing test results and coverage.
    """
    # 🎯 Default values
    total_coverage = 0
    passed_count = 0
    failed_count = 0

    # Regex to find the total coverage percentage from the 'TOTAL' line
    coverage_match = re.search(r"^TOTAL\s+.*\s+(\d+)%$", output, re.MULTILINE)
    if coverage_match:
        total_coverage = int(coverage_match.group(1))

    # Regex to find the numbers in the final summary line
    # e.g., "===... 4 failed, 1 passed in 9.85s ...==="
    summary_line_match = re.search(
        r"=+ (.*) in .*s =+", output
    )
    if summary_line_match:
        summary_text = summary_line_match.group(1)
        
        passed_match = re.search(r"(\d+)\s+passed", summary_text)
        if passed_match:
            passed_count = int(passed_match.group(1))
            
        failed_match = re.search(r"(\d+)\s+failed", summary_text)
        if failed_match:
            failed_count = int(failed_match.group(1))

    # --- Calculations ---
    total_tests = passed_count + failed_count
    pass_rate = 0.0
    if total_tests > 0:
        pass_rate = (passed_count / total_tests) * 100

    return {
        "total_coverage_percent": total_coverage,
        "pass_rate_percent": round(pass_rate, 2),
        "passed_tests": passed_count,
        "failed_tests": failed_count,
        "total_tests": total_tests,
    }

# Initialization 
def cosmic_ray_init(benchmark_name, model_name, model_generation_file, num_test_cases=5, timeout=1, num_samples=100):
    if os.path.exists(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}'):
        print(f"[+] 🧹 Cleaning up existing files in {model_name}...")
        try:
            shutil.rmtree(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}')
        except PermissionError:
            print(f"[-] PermissionError: {f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}'}")
        
    print(f"[+] 📂 Creating new directory {model_name}...")
    os.makedirs(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}')

    with open(model_generation_file, 'r') as data_handler:
        # For [json] file
        # raw_data = json.loads(data_handler.read())[:num_samples]

        # For [jsonl] file
        raw_data = [json.loads(line) for line in data_handler.readlines()[:num_samples]]
    print(f"[+] ✅ Raw data: {len(raw_data)}")

    for idx, instance in tqdm(enumerate(raw_data), desc="[+] 💾 Processing raw data"):
        os.makedirs(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/task_{idx}')

        # create 'mod.py'
        with open(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/task_{idx}/mod.py', 'w') as f:
            mod_code = ''
            mod_code += code_import + '\n\n'
            mod_code += instance['code'] + '\n\n'
            mod_code = rename_test_functions(mod_code)
            f.write(mod_code)

        # create 'test.py'
        with open(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/task_{idx}/test.py', 'w') as f:
            test_code = code_import + '\n\n' + 'from mod import *' + '\n\n'
            for test in instance['tests'][:num_test_cases]:
                test_code += f'{test}\n\n'
            # test_code += "\n\n" + "#" * 100 + "\n\n"
            f.write(test_code)         

        # create 'toml'
        with open(f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/task_{idx}/cosmic-ray.toml', 'w') as f:
            f.write(toml_template.format(model_name=model_name, task_id=idx, timeout=timeout))

def cosmic_ray_setup_wrapper(benchmark_name, model_name, task_id, num_test_cases=5):
    working_dir = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/{task_id}'
    
    # Initialize Cosmic-Ray Config
    try:
        subprocess.run(['cosmic-ray', 'init', 'cosmic-ray.toml', 'cosmic-ray.sqlite'], cwd=working_dir, check=True)
    except Exception as e:
        print(f'[-] Initialize Cosmic-Ray Error: {e}')
        return False

    # Run Cosmic-Ray Baseline
    try:
        subprocess.run(['cosmic-ray', 'baseline', 'cosmic-ray.toml'], cwd=working_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60*num_test_cases)
        return True
    except Exception as e:
        return False

def cosmic_ray_setup(benchmark_name, model_name, num_test_cases=5, sample_rate=0.1):
    # 定义输出文件路径
    correct_tasks_path = f'data/{benchmark_name}/correct_tasks_tc_{num_test_cases}_{model_name}'

    # --- 1. 获取所有通过 Pytest 的任务 (候选者) ---
    pytest_result_file = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/{model_name}_{num_test_cases}.json'
    passed_tasks_current_run = []
    
    # 尝试从 Pytest 结果中读取
    if os.path.exists(pytest_result_file):
        print(f"[+] 📖 Reading pytest results from {pytest_result_file}")
        with open(pytest_result_file, 'r') as f:
            pytest_data = json.load(f)
        
        for entry in pytest_data:
            # 确保 entry 包含必要字段
            try:
                task_id = entry.get('task_id')
                # 检查特定 k 下的通过情况
                test_counts = entry['test_at_k'][f"test@{num_test_cases}"]['result'][0]['test_counts']
                if test_counts['passed_tests'] == test_counts['total_tests'] and test_counts['total_tests'] > 0:
                    passed_tasks_current_run.append(task_id)
            except:
                continue
    else:
        raise Exception(f"[-] ⚠️ Pytest results not found! Please check {pytest_result_file}.")

    # --- 2. 获取或生成固定抽样名单 (白名单) ---
    # 文件名跟 Model，num_test_cases 无关 -> 保证 k=5 和 k=1 使用同一个抽样池
    fixed_sample_file = f'data/{benchmark_name}/fixed_sample_{int(sample_rate*100)}pct.json'
    target_sample_pool = []

    if os.path.exists(fixed_sample_file):
        # A. 加载已有名单
        print(f"[+] 📜 Loading fixed sample pool: {fixed_sample_file}")
        with open(fixed_sample_file, 'r') as f:
            target_sample_pool = json.load(f)
    else:
        # B. 首次运行，生成名单
        print(f"[+] 🎲 Generating NEW fixed sample pool...")
        working_dir = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}'
        # 获取目录下所有任务作为基底
        all_possible_tasks = [t for t in os.listdir(working_dir) if t.startswith('task_')]
        
        if sample_rate < 1.0:
            random.seed(42) # 固定种子
            sample_size = int(len(all_possible_tasks) * sample_rate)
            if sample_size == 0 and len(all_possible_tasks) > 0: sample_size = 1
            target_sample_pool = random.sample(all_possible_tasks, sample_size)
        else:
            target_sample_pool = all_possible_tasks
            
        with open(fixed_sample_file, 'w') as f:
            json.dump(target_sample_pool, f)
        print(f"[+] 💾 Saved fixed sample pool ({len(target_sample_pool)} tasks)")

    # --- 3. 计算交集：即将在本次运行 Setup 的任务 ---
    # 逻辑：必须在白名单里 AND 必须通过了当前的测试
    tasks_to_setup = list(set(target_sample_pool) & set(passed_tasks_current_run))
    
    # 简单的排序保证日志可读性
    try:
        tasks_to_setup.sort(key=lambda x: int(x.split('_')[1]))
    except:
        tasks_to_setup.sort()

    print(f"[+] 🚀 Tasks selected for mutation: {len(tasks_to_setup)} (Pool: {len(target_sample_pool)} | Passed: {len(passed_tasks_current_run)})")

    if not tasks_to_setup:
        print("[-] No tasks to setup.")
        with open(correct_tasks_path, 'w') as f: pass # 创建空文件防止报错
        return

    # --- 4. 运行 Setup (只针对筛选后的任务) ---
    task_results = process_map(
        cosmic_ray_setup_wrapper, 
        [benchmark_name]*len(tasks_to_setup), 
        [model_name]*len(tasks_to_setup), 
        tasks_to_setup, 
        [num_test_cases]*len(tasks_to_setup), 
        desc="[+] 🔄 Initialize Cosmic-Ray Mutation", 
        chunksize=1
    )
    
    # --- 5. 保存结果到 correct_tasks 文件 ---
    # mutation_run 和 mutation_statistic 会直接读取这个文件，所以它们只会在这些任务上运行
    correct_count = 0
    with open(correct_tasks_path, 'w') as f:
        for task_id, result in zip(tasks_to_setup, task_results):
            if result:
                f.write(f'{task_id}\n')
                correct_count += 1

    print(f'[+] ✅ Setup finished. Valid tasks ready for mutation: {correct_count}')

def cosmic_ray_status(benchmark_name, model_name, task, num_test_cases):
    try:
        cosmic_ray_path = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/{task}/cosmic-ray.sqlite'
        response = subprocess.run(['cr-report', cosmic_ray_path, '--show-pending'], check=True, capture_output=True, text=True)
    except Exception as e:
        print(f'[-] Error @ [{cosmic_ray_path}]: {e}')
        return (False, 0, 0)
    
    total_jobs_match = re.search(r"total jobs:\s*(\d+)", response.stdout)
    completed_jobs_match = re.search(r"complete:\s*(\d+)\s*\(", response.stdout)

    if total_jobs_match and completed_jobs_match:
        total_jobs_number = int(total_jobs_match.group(1))
        completed_jobs_number = int(completed_jobs_match.group(1))
        # print(f"[+] Task {task}: Total jobs: {total_jobs_number}, Completed jobs: {completed_jobs_number}")
        if total_jobs_number == 0: return (True, 0, 0)
        return (completed_jobs_number == total_jobs_number, total_jobs_number, completed_jobs_number)
    else:
        return (False, 0, 0)
    
def mutation_status(benchmark_name, model_name, num_test_cases):
    correct_tasks = list()
    correct_tasks_path = f'data/{benchmark_name}/correct_tasks_tc_{num_test_cases}_{model_name}'
    
    with open(correct_tasks_path, 'r') as f:
        for line in f.readlines():
            correct_tasks.append(line.strip())
    print(f'[+] ✅ Correct Tasks: {len(correct_tasks)}')
    
    for task in correct_tasks:
        completed, total_jobs_number, completed_jobs_number = cosmic_ray_status(benchmark_name, model_name, task, num_test_cases)
        if completed: 
            print(f'[+] Task {task}: Completed ({completed_jobs_number}/{total_jobs_number})')
        else: 
            print(f'[-] Task {task}: Incompleted ({completed_jobs_number}/{total_jobs_number})')

def mutation_run_wrapper(benchmark_name, model_name, num_test_cases, task):
    # cosmic-ray exec tutorial.toml tutorial.sqlite
    completed, _, _ = cosmic_ray_status(benchmark_name, model_name, task, num_test_cases)
    if completed: return

    # print(f"[+] Task {task}: Running mutations")
    working_dir = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/{task}'
    try:
        subprocess.run(['cosmic-ray', 'exec', f'cosmic-ray.toml', f'cosmic-ray.sqlite'], cwd=working_dir, check=True, timeout=360*num_test_cases)
    except subprocess.TimeoutExpired as e:
        # print(f'[-] mutation_run_wrapper, Timeout: {e}')
        pass
    except Exception as e:
        print(f'[-] mutation_run_wrapper, Error: {e}')

def mutation_run(benchmark_name, model_name, num_test_cases):
    correct_tasks = list()
    correct_tasks_path = f'data/{benchmark_name}/correct_tasks_tc_5_{model_name}'
    
    with open(correct_tasks_path, 'r') as f:
        for line in f.readlines():
            correct_tasks.append(line.strip())
    print(f'[+] ✅ Correct Tasks: {len(correct_tasks)}')

    print("================================================")
    print(f'[+] ⏱️ Start time: {datetime.datetime.now()}')
    process_map(mutation_run_wrapper, [benchmark_name]*len(correct_tasks), [model_name]*len(correct_tasks), [num_test_cases]*len(correct_tasks), correct_tasks, desc="[+] 🔮 Running mutations...")
    print(f'[+] ⏱️ End time: {datetime.datetime.now()}')

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
    print(f'[+] ✅ Correct Tasks: {len(correct_tasks)}')
    
    surviving_mutants_rate = 0.0

    statistics = process_map(mutation_statistic_wrapper, [benchmark_name]*len(correct_tasks), [model_name]*len(correct_tasks), [num_test_cases]*len(correct_tasks), correct_tasks, desc=f"[+] 🔄 Running mutation ({num_test_cases} test cases) statistics...", chunksize=1)
    for statistic in statistics:
        print(f"[+] {statistic}")
        surviving_mutants_rate += statistic["surviving_mutants_rate"]
    
    surviving_mutants_rate = (surviving_mutants_rate / len(correct_tasks)) if len(correct_tasks) > 0 else 0.0
    print(f'[+] ✅ Surviving Mutants Rate: {surviving_mutants_rate:.2%} \n')

    return surviving_mutants_rate

def pytest_run_wrapper(benchmark_name, model_name, task_id, num_test_cases):
    base_dir = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}/{task_id}'
    test_file_path = f'{base_dir}/test.py'
    source_code_path = base_dir
    
    # 默认空数据结构，防止报错
    empty_stats = [
        {"test_counts": {"passed_tests": 0, "total_tests": num_test_cases}},
        {"stmts": 0, "miss_stmts": 0, "covered_branches": 0, "total_branches": 0}
    ]

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            abs_test_file_path = os.path.abspath(test_file_path)
            abs_source_code_path = os.path.abspath(source_code_path)
            json_report_path = os.path.join(temp_dir, "coverage.json")

            coveragerc_path = os.path.join(temp_dir, ".coveragerc")
            with open(coveragerc_path, "w") as f:
                f.write("[run]\n")
                f.write("omit = *test.py\n") # 忽略所有以 test.py 结尾的文件

            # 1. 运行 Pytest 并生成 JSON 报告
            cmd = [
                'pytest', 
                abs_test_file_path, 
                f'--cov={abs_source_code_path}', 
                '--cov-branch',
                f'--cov-config={coveragerc_path}', # 指定配置文件
                f'--cov-report=json:{json_report_path}'
            ]
            
            result = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, timeout=30)
            
            # 2. 获取通过用例数 (result[0])
            # 依然使用 parse_pytest_output 解析 stdout 来获取 passed/failed 数量
            # 因为 coverage.json 里通常不包含具体的测试通过数
            stdout_metrics = parse_pytest_output(result.stdout)
            passed_tests = stdout_metrics.get("passed_tests", 0)
            total_tests_run = stdout_metrics.get("total_tests", 0)

            # 3. 获取覆盖率详情 (result[1])
            stmts = 0
            miss_stmts = 0
            covered_branches = 0
            total_branches = 0

            if os.path.exists(json_report_path):
                with open(json_report_path, 'r') as f:
                    cov_data = json.load(f)
                totals = cov_data.get('totals', {})
                
                stmts = totals.get('num_statements', 0)
                covered_lines = totals.get('covered_lines', 0)
                miss_stmts = stmts - covered_lines # 统计脚本需要 miss_stmts
                
                covered_branches = totals.get('covered_branches', 0)
                total_branches = totals.get('num_branches', 0)

            # 4. 构建符合统计脚本要求的格式
            formatted_result = [
                {
                    "test_counts": {
                        "passed_tests": passed_tests,
                        "total_tests": total_tests_run
                    }
                },
                {
                    "stmts": stmts,
                    "miss_stmts": miss_stmts,
                    "covered_branches": covered_branches,
                    "total_branches": total_branches
                }
            ]

            return {
                'model_name': model_name, 
                'task': task_id, 
                'test_at_k_data': formatted_result, # 将格式化好的数据传出去
                "status": "success"
            }
            
    except Exception as e:
        print(f"[-] Error in task {task_id}: {e}")
        return {
            'model_name': model_name, 
            'task': task_id, 
            'test_at_k_data': empty_stats, 
            "status": "error"
        }

def pytest_run(benchmark_name, model_name, num_test_cases):
    tasks = list()
    work_dir = f'data/{benchmark_name}/mutation_{num_test_cases}/{model_name}'
    
    if not os.path.exists(work_dir):
        print(f"[-] Directory not found: {work_dir}")
        return

    # 获取所有任务文件夹
    # 按照 task_0, task_1, task_2 ... 排序，保证顺序与 dataset 一致（如果统计脚本依赖顺序）
    # 这里做一个简单的根据数字排序
    all_files = os.listdir(work_dir)
    task_files = [t for t in all_files if t.startswith('task_')]
    
    # 尝试按数字排序 task_0 -> 0
    try:
        task_files.sort(key=lambda x: int(x.split('_')[1]))
    except:
        task_files.sort()

    tasks = task_files
            
    results = process_map(pytest_run_wrapper, 
                          [benchmark_name]*len(tasks), 
                          [model_name]*len(tasks), 
                          tasks, 
                          [num_test_cases]*len(tasks), 
                          desc="[+] 🔄 Running pytest", 
                          chunksize=1)
    
    # --- 核心修改：构建最终的大列表 ---
    final_output_list = []
    
    for res in results:
        # 构建统计脚本 analyze_test_at_k_results 需要的 entry 结构
        # 结构: entry["test_at_k"][f"test@{k}"]["result"]...
        
        # 这里的 key 是 "test@5" (如果 num_test_cases=5)
        k_key = f"test@{num_test_cases}"
        
        entry = {
            "task_id": res['task'], # 保留 task_id 方便调试
            "test_at_k": {
                k_key: {
                    "result": res['test_at_k_data']
                }
            }
        }
        final_output_list.append(entry)
    
    # --- 写入文件 ---
    # 注意：统计脚本用 json.load(f) 读取，所以这里必须 dump 整个列表，不能是 jsonl
    output_file = f'{work_dir}/{model_name}_{num_test_cases}.json' 
    # 我把后缀改成了 .json 以示区别，但你的统计脚本里可能是 .jsonl，请注意对应
    
    with open(output_file, 'w') as f:
        json.dump(final_output_list, f, indent=2)
        
    print(f"[+] ✅ Pytest results saved to {output_file}")

def merge_k_results(benchmark_name, model_name, k_values_list):
    print(f"[+] 🔗 Merging results for {model_name} with k={k_values_list}...")
    
    # 使用字典按 task_id 聚合数据
    # 结构: merged_data["task_0"]["test_at_k"]["test@5"] = ...
    merged_data = defaultdict(lambda: {"task_id": "", "test_at_k": {}})
    
    files_found = 0

    for k in k_values_list:
        # 对应 pytest_run 中生成的文件路径
        part_file = f'data/{benchmark_name}/mutation_{k}/{model_name}/{model_name}_{k}.json'
        
        if os.path.exists(part_file):
            files_found += 1
            with open(part_file, 'r') as f:
                data = json.load(f)
                
            for entry in data:
                t_id = entry['task_id']
                # 确保 task_id 字段存在
                merged_data[t_id]['task_id'] = t_id
                
                # 将当前的 test@k 数据更新到大字典中
                # entry['test_at_k'] 类似于 {"test@5": {...}}
                merged_data[t_id]['test_at_k'].update(entry['test_at_k'])
        else:
            print(f"[-] ⚠️ Warning: Partial result file not found: {part_file}")

    if files_found == 0:
        print(f"[-] No files found to merge for {model_name}")
        return

    # 转换为列表并排序
    final_list = list(merged_data.values())
    
    # 按 task_id 数字排序 (task_0, task_1...)
    try:
        final_list.sort(key=lambda x: int(x['task_id'].split('_')[1]))
    except:
        final_list.sort(key=lambda x: x['task_id'])

    # 保存最终合并文件
    # 建议保存在 benchmark 根目录下，或者你指定的 results 目录
    output_dir = f'data/{benchmark_name}/pytest_results'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    final_output_path = f'{output_dir}/{model_name}.json'
    
    with open(final_output_path, 'w') as f:
        json.dump(final_list, f, indent=2)
        
    print(f"[+] 🎉 Successfully merged {files_found} files into: {final_output_path}")

if __name__ == "__main__":    
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark_name", type=str, default='ULT')
    parser.add_argument("--num_samples", type=int, default=10000)
    parser.add_argument("--mode", type=str, default='all')
    args = parser.parse_args()
    
    with open('models.txt', 'r', encoding='utf-8') as f:
        model_list = f.read().splitlines()
    models = [model.split('/')[-1] for model in model_list]

    for num_test_cases in [5,2,1]:
        for model_name in models:
            cosmic_ray_init(args.benchmark_name, model_name, f'src/results/{model_name}_format.jsonl', timeout=10, num_samples=args.num_samples, num_test_cases=num_test_cases)
            pytest_run(args.benchmark_name, model_name, num_test_cases)
            cosmic_ray_setup(args.benchmark_name, model_name, num_test_cases=num_test_cases)
            mutation_status(args.benchmark_name, model_name, num_test_cases=num_test_cases)
            mutation_run(args.benchmark_name, model_name, num_test_cases)
            # mutation_statistic(args.benchmark_name, model_generation_file_path, num_test_cases, baseline_test_cases=5)

    for model_name in models:
        merge_k_results(args.benchmark_name, model_name, [5,2,1])
