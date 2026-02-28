import ast
import sys

def check_syntax(filename):
    with open(filename, 'r') as f:
        source = f.read()
    try:
        ast.parse(source)
        print(f"Syntax OK: {filename}")
        return True
    except SyntaxError as e:
        print(f"Syntax Error in {filename}: {e}")
        return False

if not check_syntax('backend/apps/deployments/services/github_webhooks.py'):
    sys.exit(1)
if not check_syntax('backend/apps/deployments/views.py'):
    sys.exit(1)
