from pathlib import Path

infile = Path('install.sh')
outfile = Path('tmp/install_sanitized.sh')
lines = infile.read_text(encoding='utf-8').splitlines()
out = []
i = 0
while i < len(lines):
    line = lines[i]
    if 'python manage.py shell -c "' in line:
        out.append(line.split('python manage.py shell -c ')[0] + 'python manage.py shell -c "<PY_SNIPPET>"')
        # skip until line that closes command string with ") before optional redirections
        i += 1
        while i < len(lines):
            if lines[i].strip().endswith('" 2>/dev/null | tr -d \'\\r\' || true)"') or \
               lines[i].strip().endswith('" 2>/dev/null || true)"') or \
               lines[i].strip().endswith('" 2>/dev/null | tr -d \'[:space:]\' || true)"') or \
               lines[i].strip().endswith('" 2>/dev/null || true)"'):
                break
            i += 1
        if i < len(lines):
            # include placeholder closing line
            out.append(lines[i].split('python manage.py shell -c ')[0] + '" 2>/dev/null | tr -d \'\\r\' || true)"' )
    else:
        out.append(line)
    i += 1

outfile.write_text('\n'.join(out) + '\n', encoding='utf-8')
print('wrote', len(out), 'lines')
