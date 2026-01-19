import ast
import json
from typing import List, Tuple
import re
import time

from sandbox_fusion import run_code, run_concurrent, RunCodeRequest, RunStatus, CommandRunStatus, RunCodeResponse
from sandbox_fusion.models import CommandRunResult
	
def extract_all_test_cases(source_code):
	extracted_assertions = []

	try:
		# 1. 解析整个代码字符串为一个 AST 模块
		module = ast.parse(source_code)
		
		# 2. 遍历模块顶层的每一个节点 (Import, FunctionDef, ClassDef...)
		for node in module.body:
			
			# 我们只关心函数定义 (def test_...):
			if isinstance(node, ast.FunctionDef):
				
				# --- 进入函数作用域：初始化新的变量环境 ---
				# env 用于存储当前函数内的变量名 -> AST节点值
				env = {} 
				
				# 遍历函数体内的每一行
				for stmt in ast.walk(node):
					
					# [A] 处理赋值语句: start = [...]
					if isinstance(stmt, ast.Assign):
						# 遍历 targets 以支持链式赋值 (e.g., a = b = 1)
						for target in stmt.targets:
							
							# --- 情况 1: 普通变量赋值 (x = 1) ---
							if isinstance(target, ast.Name):
								env[target.id] = stmt.value
								
							# --- 情况 2: 元组/列表解包 (x, y = 1, 2) ---
							elif isinstance(target, (ast.Tuple, ast.List)):
								# 只有当右边也是显式的 Tuple 或 List 时，我们要才能静态解析
								if isinstance(stmt.value, (ast.Tuple, ast.List)):
									# 确保左右元素数量一致
									if len(target.elts) == len(stmt.value.elts):
										# 遍历左边的变量名，和右边的值一一绑定
										for i, sub_target in enumerate(target.elts):
											if isinstance(sub_target, ast.Name):
												# 将 x 映射到 1, y 映射到 2
												env[sub_target.id] = stmt.value.elts[i]
							
					# [B] 处理 Assert 语句
					elif isinstance(stmt, ast.Assert):
						# 尝试提取并做变量替换
						result = process_assert_node(stmt, env)
						if result:
							extracted_assertions.append(result)
							
	except Exception as e:
		print(f"解析出错: {e}\n{source_code}")

	return extracted_assertions

def process_assert_node(assert_node, env):
    """
    处理 assert 节点，将其中引用的变量替换为 env 中的值，并返回最终的 assert 字符串。
    包含内部递归函数 resolve() 来处理深层嵌套。
    """
    
    # --- 定义内部递归函数 ---
    def resolve(node):
        # 1. 变量名替换 (Base Case)
        if isinstance(node, ast.Name):
            # 如果变量在 env 中存在，取出并递归解析它对应的值
            # (递归是为了处理 x = a; y = x; assert y 这种情况)
            if node.id in env:
                return resolve(env[node.id])
            return node

        # 2. 布尔运算 (and, or) -> 处理 reformat(...) or reformat(...)
        if isinstance(node, ast.BoolOp):
            return ast.BoolOp(
                op=node.op,
                values=[resolve(v) for v in node.values]
            )

        # 3. 比较运算 (==, !=, >, <) -> 处理 right_node 是变量的情况
        if isinstance(node, ast.Compare):
            return ast.Compare(
                left=resolve(node.left),
                ops=node.ops,
                comparators=[resolve(c) for c in node.comparators]
            )

        # 4. 一元运算 (not X) -> 处理 assert not variable
        if isinstance(node, ast.UnaryOp):
            return ast.UnaryOp(
                op=node.op,
                operand=resolve(node.operand)
            )

        # 5. 函数调用 (func(a, b)) -> 处理参数替换
        if isinstance(node, ast.Call):
            return ast.Call(
                func=resolve(node.func),
                args=[resolve(arg) for arg in node.args],
                keywords=[
                    ast.keyword(arg=kw.arg, value=resolve(kw.value)) 
                    for kw in node.keywords
                ]
            )

        # 6. 容器类型 (List, Tuple) -> 处理 assert res == [a, b]
        if isinstance(node, ast.List):
            return ast.List(elts=[resolve(e) for e in node.elts], ctx=node.ctx)
        
        if isinstance(node, ast.Tuple):
            return ast.Tuple(elts=[resolve(e) for e in node.elts], ctx=node.ctx)
            
        # 7. 二元运算 (res + 1) -> 增强健壮性
        if isinstance(node, ast.BinOp):
            return ast.BinOp(
                left=resolve(node.left),
                op=node.op,
                right=resolve(node.right)
            )
            
        # 8. 属性访问 (obj.prop) -> 增强健壮性
        if isinstance(node, ast.Attribute):
            return ast.Attribute(
                value=resolve(node.value),
                attr=node.attr,
                ctx=node.ctx
            )

        # 其他情况保持原样 (Constant 等)
        return node

    try:
        resolved_test = resolve(assert_node.test)
        return f"assert {ast.unparse(resolved_test)}"

    except Exception as e:
        return None
	
def extract_calls_answers(text_line):
	try:
		node = ast.parse(text_line).body[0]
		
		if not isinstance(node, ast.Assert): 
			return None, None
		compare = node.test

		if not isinstance(compare, ast.Compare): 
			return None, None
		
		left_node = compare.left  # Call
		right_node = compare.comparators[0] # Answer

		if not isinstance(left_node, ast.Call): 
			return None, None

		full_call_str = ast.unparse(left_node)
		answer_str = ast.unparse(right_node)
		
		return full_call_str, answer_str

	except Exception as e:
		return None, None

def safe_run_wrapper(index: int, request: RunCodeRequest):
	try:
		result = run_code(request)
		return index, result
	except Exception as e:
		return index, e

def execute_code(code: List[str], run_timeout=10) -> List[RunCodeResponse]:
	results = [None] * len(code)
	pending_indices = list(range(len(code)))
	max_retry_times = 5
	
	while max_retry_times > 0 and pending_indices:
		max_retry_times -= 1
		args_list = [
			[idx, RunCodeRequest(code=code[idx], language='python', run_timeout=run_timeout)]
			for idx in pending_indices
		]
		
		try:
			batch_outputs = run_concurrent(
				safe_run_wrapper, 
				args=args_list, 
				concurrency=20
			)
			
			new_pending_indices = []
			sandbox_needs_restart = False
			
			for output in batch_outputs:
				if not isinstance(output, tuple):
					continue
				idx, res = output
				
				if isinstance(res, Exception):
					print(f"Task {idx} failed with error: {res}")
					new_pending_indices.append(idx)
					sandbox_needs_restart = True
				else:
					results[idx] = res
			
			pending_indices = new_pending_indices
			
			if sandbox_needs_restart and pending_indices:
				print(f"Sandbox error detected. Retrying {len(pending_indices)} tasks after sleep...")
				time.sleep(60)
				
		except Exception as e:
			print(f"Concurrent error: {e}")
			time.sleep(60)

	final_output = []
	for res in results:
		if res is None:
			final_output.append(RunCodeResponse(
				status=RunStatus.Failed,
				message='Sandbox Error',
				run_result=CommandRunResult(status=CommandRunStatus.TimeLimitExceeded, stderr='Sandbox Error')
			))
		else:
			final_output.append(res)
			
	return final_output

def validate_and_fill_generated_testcases(generated_testcase_str_list: List[str], gt_code_list: List[str]) -> Tuple[List[List[str]], List[int], List[int]]:
	"""
	Validate generated test cases and fill __TO_BE_FILLED__ placeholder
	
	Args:
		generated_testcase_str_list: Generated test cases string (JSON format) list
		gt_code_list: GT code list
	
	Returns:
		(list of valid test cases list(not repeated), list of invalid test cases number(not executable, not correct, or repeated), list of all test cases number)
	"""
	valid_tests_list = []
	invalid_count_list = []
	total_count_list = []
	test_codes = []
	gen_tests = []
	
	try:
		for generated_testcase_str, gt_code in zip(generated_testcase_str_list, gt_code_list):
			invalid_count = 0

			# 1. Parse JSON with enhanced error handling
			try:
				test_data = json.loads(generated_testcase_str)
			except json.JSONDecodeError as e:
				# Try robust JSON parsing
				try:
					# Fix common JSON problems
					fixed_str = generated_testcase_str.replace("'", '"')
					# Remove trailing comma
					fixed_str = re.sub(r',\s*}', '}', fixed_str)
					fixed_str = re.sub(r',\s*]', ']', fixed_str)
					test_data = json.loads(fixed_str)
				except Exception as e:
					raise Exception(f"JSON parsing also failed: {e}")
			
			# 2. Validate JSON structure
			if not isinstance(test_data, dict):
				raise Exception(f"Test data is not a dictionary: {type(test_data)}")
				
			if "assert_statements" not in test_data:
				raise Exception(f"Test data is missing 'assert_statements' field, available fields: {list(test_data.keys())}")
			
			assert_statements = test_data["assert_statements"]
			
			# Handle assert_statements not being a list
			if isinstance(assert_statements, str):
				# If it's a string, split by lines
				assert_statements = [line.strip() for line in assert_statements.split('\n') 
								if line.strip() and line.strip().startswith('assert')]
			elif not isinstance(assert_statements, list):
				raise Exception(f"assert_statements is not a list or string: {type(assert_statements)}")
			
			# 3. Quick check if function is defined in GT code (string check)
			# if f"def {fn_name}(" not in gt_code:
			# 	# Extract actual function name defined in GT code, for error message
			# 	func_definitions = re.findall(r'def\s+(\w+)\s*\(', gt_code)
			# 	raise Exception(f"Function '{fn_name}' is not defined in GT code, available functions: {func_definitions}\nGT code: \n{gt_code}")
			
			# 4. Handle each assert statement
			normalized_stmts = []
			calls = []
			answers = []
			total_count = len(assert_statements)
			for i, stmt in enumerate(assert_statements):
				if not isinstance(stmt, str):
					print(f"assert statement {i+1} is not a string: {type(stmt)}")
					invalid_count += 1
					continue
					
				stmt = stmt.strip()
				func_call_part, answer_part = extract_calls_answers(stmt)
				if not func_call_part or not answer_part:
					invalid_count += 1
					continue
				stmt = f"assert {func_call_part} == __TO_BE_FILLED__"
				
				normalized_stmts.append(stmt)
				calls.append(func_call_part)
				answers.append(answer_part)
				
			invalid_count_list.append(invalid_count)
			total_count_list.append(total_count)

			lines = []
			lines.append("# GT code")
			lines.append(gt_code)
			lines.append("# Generated testcases")
			for idx, (call, answer) in enumerate(zip(calls, answers)):
				lines.append(f"print('__CASE_START__{idx}')")
				lines.append("try:")
				lines.append(f"    _r = {call}")
				lines.append(f"    print('__CASE_RES__{idx}:' + repr(_r))")
				# lines.append(f"    print('__CASE_ANS__{idx}:{answer}')")
				lines.append(f"    print('__CASE_VAL__{idx}:' + repr(((_r) == ({answer}))))")
				lines.append("except Exception as _e:")
				lines.append(f"    print('__CASE_ERR__{idx}:' + repr(_e))")
			test_codes.append('\n'.join(lines))

			gen_tests.append(normalized_stmts)
		
		# Run codes in sandbox parallelly
		results = execute_code(test_codes)

		for i, resp in enumerate(results):
			stdout = resp.run_result.stdout if (resp.status == RunStatus.Success and resp.run_result and resp.run_result.status == CommandRunStatus.Finished) else ""
			valid_tests = set()
			invalid_count = invalid_count_list[i]
			normalized_stmts = gen_tests[i]

			for idx, stmt in enumerate(normalized_stmts):
				key_res = f"__CASE_RES__{idx}:"
				key_val = f"__CASE_VAL__{idx}:"
				key_err = f"__CASE_ERR__{idx}"
				if key_res in stdout:
					line = next((ln for ln in stdout.splitlines() if key_res in ln), "")
					result_repr = line.split(key_res, 1)[1].strip()
					processed_stmt = stmt.replace("__TO_BE_FILLED__", result_repr)					
					# check if the test case is repeated
					if processed_stmt in valid_tests:
						invalid_count += 1
						continue
					valid_tests.add(processed_stmt)	
					# check if the test case is correct
					line = next((ln for ln in stdout.splitlines() if key_val in ln), "")
					if not line:
						invalid_count += 1
						continue
					val_repr = line.split(key_val, 1)[1].strip()
					if val_repr == "False":
						invalid_count += 1
				elif key_err in stdout:
					invalid_count += 1
				else:
					invalid_count += 1

			valid_tests_list.append(list(valid_tests))
			invalid_count_list[i] = invalid_count
	
	except Exception as e:
		raise Exception(f"Validation process encountered severe error: {e}")
	
	return valid_tests_list, invalid_count_list, total_count_list