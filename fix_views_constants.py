with open('views_old.py', 'r') as f:
    views_lines = f.readlines()

def get_block(start_str):
    block = []
    in_block = False
    for line in views_lines:
        if line.startswith(start_str):
            in_block = True
            block.append(line)
        elif in_block:
            if line.startswith(')') or line.startswith('}') or line.strip() == '':
                if line.startswith(')') or line.startswith('}'):
                    block.append(line)
                break
            else:
                block.append(line)
    return ''.join(block)

env_pattern = get_block('_ENV_KEY_PATTERN =')
masked_pattern = get_block('_MASKED_SECRET_PATTERN =')
block_size = get_block('_BACKUP_DOWNLOAD_BLOCK_SIZE =')
content_type = get_block('_BACKUP_DOWNLOAD_CONTENT_TYPE =')
local_target = get_block('_LOCAL_DEPLOY_TARGET_VALUES = {')
missing_target = get_block('_DEPLOY_TARGET_MISSING =')

with open('backend/apps/deployments/views_envvars.py', 'r') as f:
    c = f.read()
c = c.replace('import logging\nlogger = logging.getLogger(__name__)\n', 'import logging\nlogger = logging.getLogger(__name__)\n' + env_pattern + masked_pattern + '\n')
with open('backend/apps/deployments/views_envvars.py', 'w') as f:
    f.write(c)

with open('backend/apps/deployments/views_files.py', 'r') as f:
    c = f.read()
c = c.replace('import logging\nlogger = logging.getLogger(__name__)\n', 'import logging\nlogger = logging.getLogger(__name__)\n' + block_size + content_type + '\n')
with open('backend/apps/deployments/views_files.py', 'w') as f:
    f.write(c)

with open('backend/apps/deployments/views_service.py', 'r') as f:
    c = f.read()
c = c.replace('import logging\nlogger = logging.getLogger(__name__)\n', 'import logging\nlogger = logging.getLogger(__name__)\n' + local_target + missing_target + '\nfrom .tasks import _IN_PROGRESS_DEPLOYMENT_STATUSES\n')
with open('backend/apps/deployments/views_service.py', 'w') as f:
    f.write(c)
