import ast

def get_global_function_source(filepath, func_names):
    with open(filepath, 'r') as f:
        source = f.read()
    lines = source.split('\n')
    tree = ast.parse(source)
    res = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in func_names:
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            res[node.name] = '\n'.join(lines[start-1:node.end_lineno])
        if isinstance(node, ast.ClassDef) and node.name in func_names:
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            res[node.name] = '\n'.join(lines[start-1:node.end_lineno])
    return res
