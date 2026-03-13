from pathlib import Path
import subprocess
path=Path('install.sh')
lines=path.read_text().splitlines()
for n in range(1, len(lines)+1):
    tmp=Path('/tmp/p.sh')
    tmp.write_text('\n'.join(lines[:n])+'\n', encoding='utf-8')
    r=subprocess.run(['bash','-n', str(tmp)], capture_output=True, text=True)
    if r.returncode != 0:
        print('FAIL_AT', n)
        print(r.stderr.strip())
        break
else:
    print('OK', len(lines))
