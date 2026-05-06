import sys
import re

def trace_blocks(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove comments
    content = re.sub(r'#.*', '', content)

    # Remove heredocs
    # Use a regex that matches from <<'EOF' to EOF across multiple lines
    # This is hard because the token varies.
    # Let's use the line-by-line approach but keep state.
    lines = content.splitlines()
    clean_lines = []
    skip_until = None
    in_quote = None # ' or "
    
    for i, line in enumerate(lines):
        line_num = i + 1
        
        # Handle heredocs
        if skip_until:
            if line.strip() == skip_until:
                skip_until = None
            clean_lines.append("") 
            continue
        
        # Match heredoc start
        match = re.search(r'<<-?\s*[\'"]?(\w+)[\'"]?', line)
        if match:
            skip_until = match.group(1)
            line = line[:match.start()]
            
        # Handle multi-line quotes (simplified)
        # If a line has an odd number of quotes, we toggle in_quote
        # This is a heuristic but often works for simple scripts
        for char in line:
            if char in ("'", '"'):
                if in_quote == char:
                    in_quote = None
                elif in_quote is None:
                    in_quote = char
        
        if in_quote:
            clean_lines.append("")
        else:
            clean_lines.append(line)
    
    clean_content = '\n'.join(clean_lines)
    
    # Trace keywords with line numbers
    tokens = []
    # We want to scan the clean_lines (which have heredocs removed)
    # But clean_lines is a list of lines. Let's join them with newlines.
    clean_content = '\n'.join(clean_lines)
    
    for match in re.finditer(r'\b(if|fi|case|esac|for|while|done)\b', clean_content):
        line_num = clean_content.count('\n', 0, match.start()) + 1
        tokens.append((line_num, match.group(0)))
    
    # Stack trace for IF/FI
    stack = []
    for line_num, token in tokens:
        if token == 'if':
            stack.append(line_num)
        elif token == 'fi':
            if not stack:
                print(f"Error: EXTRA 'fi' at line {line_num}")
            else:
                stack.pop()
    
    for line_num in stack:
        print(f"Error: UNCLOSED 'if' started at line {line_num}")

if __name__ == "__main__":
    trace_blocks(sys.argv[1])
