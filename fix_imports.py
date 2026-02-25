import re

file_path = 'backend/apps/deployments/tasks.py'

with open(file_path, 'r') as f:
    lines = f.readlines()

# Find the block of imports
start_line = 0
end_line = 0
for i, line in enumerate(lines):
    if line.startswith('"""Tasks module."""'):
        continue
    if line.startswith('import ') or line.startswith('from '):
        if start_line == 0:
            start_line = i
        end_line = i

# Separate the block
import_block = lines[start_line:end_line+1]
remaining_code = lines[end_line+1:]

# Categorize imports
stdlib = []
third_party = []
django = []
local_apps = []
services = []
relative = []

for line in import_block:
    line = line.strip()
    if not line: continue

    if line.startswith('import '):
        if line.startswith('import requests'): third_party.append(line)
        else: stdlib.append(line)
    elif line.startswith('from '):
        module = line.split()[1]
        if module.startswith('django'):
            django.append(line)
        elif module.startswith('celery'):
            third_party.append(line)
        elif module.startswith('urllib'):
            stdlib.append(line)
        elif module.startswith('apps.'):
            local_apps.append(line)
        elif module.startswith('services.'):
            services.append(line)
        elif module.startswith('.'):
            # Convert relative to absolute for apps.deployments
            if 'backup_service' in line:
                local_apps.append('from apps.deployments.services.backup_service import BackupService')
            elif 'transfer_service' in line:
                local_apps.append('from apps.deployments.services.transfer_service import ServerTransferService')
            elif 'models_backup' in line:
                local_apps.append('from apps.deployments.models_backup import BackupSchedule, ServiceBackup')
            elif 'models_transfer' in line:
                local_apps.append('from apps.deployments.models_transfer import ServerTransfer')
            else:
                relative.append(line) # Should not happen based on previous steps but good fallback
        else:
            third_party.append(line)

# Sort within categories
stdlib.sort()
third_party.sort()
django.sort()
local_apps.sort()
services.sort()

# Reconstruct
new_imports = []
new_imports.extend(stdlib)
new_imports.append('')
new_imports.extend(third_party)
new_imports.append('')
new_imports.extend(django)
new_imports.append('')
new_imports.extend(local_apps)
new_imports.append('')
new_imports.extend(services)
new_imports.append('')

# Add special handling for the utility import which was multiline
# For now, I'll just hardcode the known structure if it's too complex, but let's try to be generic or just fix the specific issue.
# The specific issue is  imports are split by  imports.

# Let's just manually construct the block based on the file content I read
manual_block = [
    'import logging',
    'import re',
    'import shutil',
    'import tempfile',
    'import subprocess',
    'import os',
    'import json',
    'import zipfile',
    'from urllib.parse import unquote, urlparse',
    '',
    'import docker',
    'import requests',
    'from celery import shared_task',
    '',
    'from django.conf import settings',
    'from django.utils import timezone',
    'from django.db.models import Sum',
    '',
    'from apps.billing.models import UsageRecord, UserSubscription, Invoice, PricingPlan, DailyRevenue, InfrastructureCost',
    'from apps.billing.services.metering import UsageMeter',
    'from apps.cloud.models import CloudProvider',
    'from apps.cloud.services.builder import NixpacksBuilder',
    'from apps.cloud.services.compute import ComputeService',
    'from apps.cloud.services.function_provisioner import FunctionProvisioner',
    'from apps.deployments.models import Service, Deployment, EnvironmentVariable, PlatformConfig',
    'from apps.deployments.models_addons import Addon, Backup',
    'from apps.deployments.models_backup import BackupSchedule, ServiceBackup',
    'from apps.deployments.models_storage import Volume',
    'from apps.deployments.models_transfer import ServerTransfer',
    'from apps.deployments.services.backup_service import BackupService',
    'from apps.deployments.services.pipeline import PipelineManager, PipelineError',
    'from apps.deployments.services.transfer_service import ServerTransferService',
    'from apps.deployments.utils import (',
    '    append_log,',
    '    broadcast_status,',
    '    update_stage,',
    ')',
    '',
    'from services.addon_provisioner import addon_provisioner',
]

# Write the new file
with open(file_path, 'w') as f:
    f.write('"""Tasks module."""\n')
    for line in manual_block:
        f.write(line + '\n')

    # Skip until logger = ...
    skip = True
    for line in lines:
        if line.startswith('logger = logging.getLogger(__name__)'):
            skip = False
        if not skip:
            f.write(line)

print("File rewritten")
