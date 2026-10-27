path = "backend/apps/deployments/services/transfer_service.py"
with open(path, "r") as f:
    lines = f.readlines()
# line 1381 (0-indexed 1380) - the self._node_api_request call
lines[1380] = "                self._node_api_request('incoming/upload-file', body={\n"
with open(path, "w") as f:
    f.writelines(lines)
print("Fixed")
