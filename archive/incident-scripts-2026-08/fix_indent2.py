path = "backend/apps/deployments/services/transfer_service.py"
with open(path, "r") as f:
    lines = f.readlines()

# Remove the duplicate bad line (line 1380, 0-indexed 1379) and fix the good one
# Current state:
# 1379: b64 line (correct)
# 1380: bad duplicate (1 space indent, missing path key)
# 1381: good duplicate (16 spaces indent)
# 1382: 'content_base64' key line
# 1383: closing }) line
# 1384: offset line

# Replace lines 1380-1383 (0-indexed 1379-1382) with the correct block
new_block = [
    "                self._node_api_request('incoming/upload-file', body={\n",
    "                    'path': remote_backup,\n",
    "                    'content_base64': b64,\n",
    "                })\n",
]
lines[1379:1383] = new_block

with open(path, "w") as f:
    f.writelines(lines)
print("Fixed")
