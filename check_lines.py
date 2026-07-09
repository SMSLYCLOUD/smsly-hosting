path = "backend/apps/deployments/services/transfer_service.py"
with open(path, "r") as f:
    lines = f.readlines()
for i in range(1378, min(1387, len(lines))):
    print(f"{i+1}: {lines[i]!r}")
