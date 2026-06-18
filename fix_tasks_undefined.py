import re
import glob
from make_tasks_mapping import mapping

# parse constants from tasks.py.
constants = {}
with open('backend/apps/deployments/tasks.py', 'r') as f:
    lines = f.readlines()
    for line in lines:
        if line.startswith('_IN_PROGRESS_DEPLOYMENT_STATUSES ='):
            constants['_IN_PROGRESS_DEPLOYMENT_STATUSES'] = "from .tasks_deploy import _IN_PROGRESS_DEPLOYMENT_STATUSES"
        elif line.startswith('MAINTENANCE_ACTIONS ='):
            constants['MAINTENANCE_ACTIONS'] = "from .tasks_maintenance import MAINTENANCE_ACTIONS"

func_to_mod = {}
for mod, funcs in mapping.items():
    for f in funcs:
        func_to_mod[f] = f"from .{mod} import {f}"

for file in glob.glob('backend/apps/deployments/tasks_*.py'):
    with open(file, 'r') as f:
        content = f.read()

    new_imports = set()

    # functions
    for f_name, imp in func_to_mod.items():
        if re.search(r'\b' + f_name + r'\b', content) and not ('def ' + f_name in content) and not ('class ' + f_name in content):
            new_imports.add(imp)

    # constants
    for key, imp in constants.items():
        if key in content and not (key + ' =' in content):
            new_imports.add(imp)

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
