with open("install.sh", "r") as f:
    lines = f.readlines()

for i in range(1385, 1405):
    if i < len(lines):
        print(f"Line {i}: {lines[i].rstrip()}")
