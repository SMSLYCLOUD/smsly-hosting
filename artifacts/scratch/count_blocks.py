import sys
import re

def count_blocks(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove comments
    content = re.sub(r'#.*', '', content)

    # Remove heredocs
    # This is a bit tricky, but let's try to find <<EOF ... EOF
    # Simple version: find lines with << and skip until the end token
    lines = content.splitlines()
    clean_lines = []
    skip_until = None
    
    for line in lines:
        if skip_until:
            if line.strip() == skip_until:
                skip_until = None
            continue
        
        match = re.search(r'<<-?\s*[\'"]?(\w+)[\'"]?', line)
        if match:
            skip_until = match.group(1)
            # Still process the part of the line before <<
            line = line[:match.start()]
            
        clean_lines.append(line)
    
    clean_content = '\n'.join(clean_lines)
    
    # Count keywords
    # Use word boundaries \b
    ifs = len(re.findall(r'\bif\b', clean_content))
    fis = len(re.findall(r'\bfi\b', clean_content))
    cases = len(re.findall(r'\bcase\b', clean_content))
    esacs = len(re.findall(r'\besac\b', clean_content))
    fors = len(re.findall(r'\bfor\b', clean_content))
    dones = len(re.findall(r'\bdone\b', clean_content))
    whiles = len(re.findall(r'\bwhile\b', clean_content))
    
    print(f"File: {filename}")
    print(f"if: {ifs}, fi: {fis} (Diff: {ifs - fis})")
    print(f"case: {cases}, esac: {esacs} (Diff: {cases - esacs})")
    print(f"for: {fors}, done: {dones} (Diff: {fors - dones})")
    print(f"while: {whiles}, done: {dones} (Note: for/while share done)")

if __name__ == "__main__":
    count_blocks(sys.argv[1])
