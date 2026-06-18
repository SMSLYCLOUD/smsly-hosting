import glob

def fix_file(file):
    with open(file, 'r') as f:
        lines = f.readlines()

    # find where imports start and end (to append)
    for i, line in enumerate(lines):
        if line.startswith('class ') or line.startswith('def ') or line.startswith('@'):
            idx = i
            break

    # well wait, in `run_extraction.py` I used `content.find('def ')`. I'll just re-run it but fix the `content.find` part!
