import subprocess
import re

with open("backend/apps/deployments/views.py", "r") as f:
    content = f.read()

service_backup_patch = """    def perform_destroy(self, instance):
        import os
        import logging
        if instance.file_path and os.path.exists(instance.file_path):
            try:
                os.remove(instance.file_path)
            except OSError as e:
                logging.getLogger(__name__).warning("Failed to delete backup file %s: %s", instance.file_path, e)
        instance.delete()

    def perform_create(self, serializer):"""

server_backup_patch = """    def perform_destroy(self, instance):
        import os
        import logging
        if instance.file_path and os.path.exists(instance.file_path):
            try:
                os.remove(instance.file_path)
            except OSError as e:
                logging.getLogger(__name__).warning("Failed to delete server backup file %s: %s", instance.file_path, e)
        instance.delete()

    def perform_create(self, serializer):"""

content = content.replace(
    "    def perform_create(self, serializer):\n        backup = serializer.save(created_by=self.request.user, status='PENDING')",
    service_backup_patch + "\n        backup = serializer.save(created_by=self.request.user, status='PENDING')"
)

content = content.replace(
    "    def perform_create(self, serializer):\n        backup = serializer.save(status='PENDING')",
    server_backup_patch + "\n        backup = serializer.save(status='PENDING')"
)

with open("backend/apps/deployments/views.py", "w") as f:
    f.write(content)

