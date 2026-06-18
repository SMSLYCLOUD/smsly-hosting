import os
import glob
import re
from make_tasks_mapping import mapping

# inverted mapping
func_to_mod = {}
for mod, funcs in mapping.items():
    for f in funcs:
        func_to_mod[f] = mod

for file in glob.glob('backend/apps/deployments/tasks_*.py'):
    with open(file, 'r') as f:
        content = f.read()

    new_imports = set()

    for f_name, mod_name in func_to_mod.items():
        if re.search(r'\b' + f_name + r'\b', content) and not ('def ' + f_name in content) and not ('class ' + f_name in content):
            new_imports.add(f"from .{mod_name} import {f_name}")

    if new_imports:
        parts = content.split('\n\n')
        idx = 0
        for i, p in enumerate(parts):
            if 'import ' in p or 'from ' in p:
                pass
            if 'def ' in p or 'class ' in p or '@' in p:
                idx = i
                break

        parts[idx-1] += '\n' + '\n'.join(new_imports)

        with open(file, 'w') as f:
            f.write('\n\n'.join(parts))
