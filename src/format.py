import os
import textwrap
import ast
from pathlib import Path
from argparse import ArgumentParser
import json
import re

def extract_and_wrap_test(code, func_name):
    """
    遍历代码寻找第一个 assert {func_name}。
    - 如果在顶层找到：保留之前的上下文 + 该 assert。
    - 如果在函数定义(FunctionDef)内找到：保留之前的上下文 + 函数内截取到的代码。
    - 最后统一包裹在 def test_{func_name} 中。
    """
    try:
        code = textwrap.dedent(code)
        tree = ast.parse(code)
        
        context_nodes = [] # 用于积累 import, helper func, 变量定义等上下文
        
        # 检查节点是否是针对 func_name 的 assert
        def is_target_assert(node, name):
            if isinstance(node, ast.Assert):
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and child.id == name:
                        return True
            return False
        
        # 将一组 AST 节点包裹进 def test_{func_name}
        def wrap_nodes_in_func(nodes, func_name):
            new_func = ast.FunctionDef(
                name=f"test_{func_name}",
                args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                body=nodes,
                decorator_list=[]
            )
            new_func = ast.fix_missing_locations(new_func)
            return ast.unparse(new_func)

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                sliced_inner_body = []
                
                for inner_node in node.body:
                    sliced_inner_body.append(inner_node)
                    if is_target_assert(inner_node, func_name):
                        final_body = context_nodes + sliced_inner_body
                        return wrap_nodes_in_func(final_body, func_name)
                        
            elif isinstance(node, ast.Assert):
                if is_target_assert(node, func_name):
                    context_nodes.append(node)
                    return wrap_nodes_in_func(context_nodes, func_name)
                else:
                    # 是 assert 但不是测我们要的函数，忽略（跳过该节点）
                    continue
            
            context_nodes.append(node)

    except Exception as e:
        print(f"AST Error: {e}\n{code}")
        
    # 如果遍历完都没找到，返回基础错误测试函数
    return f"def test_wrong():\n    assert 1 == 0"

# from data_utils import read_jsonl, write_jsonl
def read_jsonl(path):
    data=[]
    with open(path,'r') as f:
        for line in f:
            data.append(json.loads(line))
    return data


def write_jsonl(data,path):
    with open(path,'w') as f:
        for d in data:
            f.write(json.dumps(d)+'\n')


def change_function_name(code, new_name):
    try:
        # Parse the code into an AST
        tree = ast.parse(code)

        # Find the first function definition and change its name
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # if node.name!=new_name:
                #     node.name = new_name
                #     break
                # else:
                #     break
                node.name = new_name

        # Convert the modified AST back to code
        new_code = ast.unparse(tree)
        return new_code
    except Exception as e: #cannoot parse
        return code


def reformat_case_byrules(testcase, func_name, lang='python', idx=0):
    if testcase.startswith(' '): #remove extra indents (encountered in codellama, mistral-7b starts with one space...)
        testcase=textwrap.dedent(testcase)
    lines=testcase.split('\n')

    if lang=='python':
        last_line=lines[-1] #if last line is not complete (due to token limit), remove it    
        last_line=textwrap.dedent(last_line)
        try:
            compile(last_line,'<string>','exec')
        except:
            #print('imcomplete last line, remove it', last_line)
            lines=lines[:-1] #last line cannot compile

    testcase='\n'.join(lines)
    testcase=change_function_name(testcase, f'{func_name}_{idx}')
    return testcase


def remove_extra(testcase, func_name, lang='python'):
    """Remove extra test inputs and natural language descriptions before and after the test method.
    Only keep the contents between def test() and {func_name}"""
    lines=testcase.split('\n')
    func_startline=-1 #the line when test function starts (def test....)
    for i in range(len(lines)):
        if lines[i].find('def test')>=0:
            func_startline=i
            break
    if func_startline>=0:
        test_endline=len(lines)
        for i in range(len(lines)):
            if lines[i].find(f'assert {func_name}')>=0: #first call to the function under test
                test_endline=i+1
                break
        new_testcase='\n'.join(lines[func_startline:test_endline])
        return new_testcase
    else:
        matches = re.findall(r"```python(.*?)```", testcase, re.DOTALL)
        return extract_and_wrap_test('\n'.join(matches), func_name)


def reformat_line(datapath,newpath):
    data=read_jsonl(datapath)
    formatted_data=[]
    for e in data:
        code=e['code']
        func_name=e['func_name']
        test_funcname=f'test_{func_name}'
        #print(code)
        tests=e['tests']
        #formated_tests=[]
        for lineno in tests:
            testcase=tests[lineno]
            print(testcase)
            testcase=remove_extra(testcase, func_name)
            reformatted_testcase=reformat_case_byrules(testcase, test_funcname, 'python')
            #print('------')
            print(reformatted_testcase)
            print('<---------------------->')
            tests[lineno]=reformatted_testcase
        e['tests']=tests

        formatted_data.append(e)
    write_jsonl(formatted_data, newpath)


def reformat_branch(datapath,newpath):
    data=read_jsonl(datapath)
    formatted_data=[]
    for e in data:
        code=e['code']
        func_name=e['func_name']
        test_funcname=f'test_{func_name}'
        #print(code)
        tests=e['tests']
        formated_tests=[]
        for branch in tests:
            testcase=branch['test']
            print(testcase)
            testcase=remove_extra(testcase, func_name)
            reformatted_testcase=reformat_case_byrules(testcase, test_funcname, 'python')
            #print('------')
            print(reformatted_testcase)
            print('<---------------------->')
            branch['test']=reformatted_testcase
            formated_tests.append(branch)
        e['tests']=formated_tests

        formatted_data.append(e)
    write_jsonl(formatted_data, newpath)


def reformat_cov(datapath,newpath):
    data=read_jsonl(datapath)
    formatted_data=[]
    for e in data:
        #print(code)
        func_name=e['func_name']
        test_funcname=f'test_{func_name}'
        formatted_test_cases=[]
        testcases=e['tests']
        for idx, testcase in enumerate(testcases):
            # print(testcase)
            extracted_testcase=remove_extra(testcase, func_name)
            #if extracted_testcase!=testcase:
                #print(testcase)
                #print('----')
                #print(extracted_testcase)
            reformatted_testcase=reformat_case_byrules(extracted_testcase, test_funcname, 'python', idx)
            # print('------')
            # print(reformatted_testcase)
            # print('<---------------------->')
            formatted_test_cases.append(reformatted_testcase)
        e['tests']=formatted_test_cases

        formatted_data.append(e)
    write_jsonl(formatted_data, newpath)


def parse_args():
    parser = ArgumentParser()
    parser.add_argument("--path", type=str, default='')
    parser.add_argument("--mode", type=str, default='overall', choices=['line', 'branch', 'overall'])
    return parser.parse_args()


if __name__=='__main__':
    args=parse_args()
    with open('../models.txt', 'r', encoding='utf-8') as f:
        model_list = f.read().splitlines()

    for model in model_list:
        args.path = f'{model.split("/")[-1]}.jsonl'
        if not os.path.exists(f"./results/{args.path}"):
            print(f"{model} does not exists")
            continue
        print('generated answers:', args.path)
        print('coverage mode:', args.mode)
        output_dir = Path('results')
        finename,ext=os.path.splitext(args.path)
        newpath=f'{finename}_format{ext}'
        print(newpath)
        if args.mode=='line':
            print('reformat line coverage')
            reformat_line(output_dir / args.path, output_dir / newpath)
        elif args.mode=='overall':
            print('reformat overall coverage')
            reformat_cov(output_dir / args.path, output_dir / newpath)
        elif args.mode=='branch':
            print('reformat branch coverage')
            reformat_branch(output_dir / args.path, output_dir / newpath)
