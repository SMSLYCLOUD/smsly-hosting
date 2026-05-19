import re

with open("backend/apps/deployments/tests/test_deletion_views.py", "r") as f:
    content = f.read()

content = content.replace("mock_delay.assert_called_once_with(str(self.service.id))", "mock_delay.assert_called_once_with(str(self.service.id), force=False)")

with open("backend/apps/deployments/tests/test_deletion_views.py", "w") as f:
    f.write(content)
