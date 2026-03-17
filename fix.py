import re

with open("backend/apps/deployments/services/transfer_service.py", "r") as f:
    content = f.read()

content = content.replace("        finally:\n            import os\n            os.unlink(local_script.name)", "        finally:\n            import os as _os\n            _os.unlink(local_script.name)")

with open("backend/apps/deployments/services/transfer_service.py", "w") as f:
    f.write(content)
