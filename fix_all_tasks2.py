import ast
import split_tools
from make_tasks_mapping import mapping

with open('backend/apps/deployments/tasks.py', 'r') as f:
    source = f.read()

tree = ast.parse(source)

import_lines = []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        import_lines.append(ast.unparse(node))

imports = '\n'.join(import_lines)

for mod, funcs in mapping.items():
    mod_code = split_tools.get_global_function_source('backend/apps/deployments/tasks.py', funcs)
    out = []
    out.append("import logging")
    out.append("logger = logging.getLogger(__name__)")
    out.append(imports)
    out.append("\n\n")

    for f in funcs:
        if f in mod_code:
            out.append(mod_code[f])
            out.append("\n\n")

    with open(f'backend/apps/deployments/{mod}.py', 'w') as f:
        f.write('\n'.join(out))
