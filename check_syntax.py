import sys
import re

def check_nesting(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    stack = []
    line_num = 0
    for line in lines:
        line_num += 1
        # Strip comments
        code = re.sub(r'#.*', '', line).strip()
        if not code:
            continue

        # Look for keywords as standalone words (word boundaries)
        # This is still a heuristic but better than before.
        
        # Opening
        if re.search(r'\bif\b', code) and not re.search(r'\bfi\b', code):
            stack.append(('if', line_num, line.strip()))
        if re.search(r'\bcase\b', code):
            stack.append(('case', line_num, line.strip()))
        if re.search(r'\bfor\b', code) and not re.search(r'\bdone\b', code):
            stack.append(('for', line_num, line.strip()))
        if re.search(r'\bwhile\b', code) and not re.search(r'\bdone\b', code):
            stack.append(('while', line_num, line.strip()))

        # Closing
        if re.search(r'\bfi\b', code) and not re.search(r'\bif\b', code):
            if not stack or stack[-1][0] != 'if':
                print(f"Error: unexpected 'fi' at line {line_num}: {line.strip()}")
            else:
                stack.pop()
        if re.search(r'\besac\b', code):
            if not stack or stack[-1][0] != 'case':
                print(f"Error: unexpected 'esac' at line {line_num}: {line.strip()}")
            else:
                stack.pop()
        if re.search(r'\bdone\b', code):
            if not stack or stack[-1][0] not in ('for', 'while'):
                print(f"Error: unexpected 'done' at line {line_num}: {line.strip()}")
            else:
                stack.pop()

    if stack:
        print("\nUnclosed blocks found:")
        for kind, lnum, lcontent in stack:
            # Avoid printing non-ascii chars to avoid console errors
            safe_content = "".join(c if ord(c) < 128 else "?" for c in lcontent)
            print(f"  {kind} at line {lnum}: {safe_content}")

if __name__ == "__main__":
    check_nesting(sys.argv[1])
