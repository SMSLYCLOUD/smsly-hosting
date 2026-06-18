with open('backend/apps/deployments/tasks.py', 'r') as f:
    lines = f.readlines()

def get_block(start_str):
    block = []
    in_block = False
    for line in lines:
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

ollama = []
for c in ['SHARED_OLLAMA_RAM_FRACTION =', 'SHARED_OLLAMA_MIN_RAM_MB =', 'SHARED_OLLAMA_MAX_RAM_MB =',
          'SHARED_OLLAMA_MIN_CPU_CORES =', 'SHARED_OLLAMA_MAX_CPU_CORES =', 'SHARED_OLLAMA_NAME_PREFIX =',
          'SHARED_OLLAMA_PORT =']:
    for line in lines:
        if line.startswith(c):
            ollama.append(line)
            break

ai_router_content = open('backend/apps/deployments/tasks_ai_router.py').read()
ai_router_content = ai_router_content.replace('import logging\nlogger = logging.getLogger(__name__)\n',
                                              'import logging\nlogger = logging.getLogger(__name__)\n' + ''.join(ollama) + '\n')
open('backend/apps/deployments/tasks_ai_router.py', 'w').write(ai_router_content)

db_map = get_block('_SERVICE_DB_MAP = {')
url_patterns = get_block('_SERVICE_URL_PATTERNS = {')
secrets = get_block('_PROPAGATED_SECRETS = {')
redis_db = get_block('_SERVICE_REDIS_DB = {')

deploy_local = open('backend/apps/deployments/tasks_deploy_local.py').read()
deploy_local = deploy_local.replace('import logging\nlogger = logging.getLogger(__name__)\n',
                                    'import logging\nlogger = logging.getLogger(__name__)\n' + db_map + '\n' + url_patterns + '\n' + secrets + '\n' + redis_db + '\n')
open('backend/apps/deployments/tasks_deploy_local.py', 'w').write(deploy_local)

update_log = get_block('REMOTE_UPDATE_LOG_LIMIT =')
server_update = open('backend/apps/deployments/tasks_server_update.py').read()
server_update = server_update.replace('import logging\nlogger = logging.getLogger(__name__)\n',
                                      'import logging\nlogger = logging.getLogger(__name__)\n' + update_log + '\n')
open('backend/apps/deployments/tasks_server_update.py', 'w').write(server_update)

auto_approve = get_block('AUTO_APPROVE_COMMIT_MARKERS = (')
tasks_utils = open('backend/apps/deployments/tasks_utils.py').read()
tasks_utils = tasks_utils.replace('import logging\nlogger = logging.getLogger(__name__)\n',
                                  'import logging\nlogger = logging.getLogger(__name__)\n' + auto_approve + '\n')
open('backend/apps/deployments/tasks_utils.py', 'w').write(tasks_utils)

# hashlib and hmac in health
health = open('backend/apps/deployments/tasks_health.py').read()
health = health.replace('import logging\nlogger = logging.getLogger(__name__)\n',
                        'import logging\nlogger = logging.getLogger(__name__)\nimport hashlib\nimport hmac\n')
open('backend/apps/deployments/tasks_health.py', 'w').write(health)

# AIProviderSettings in tasks_deploy.py
deploy = open('backend/apps/deployments/tasks_deploy.py').read()
deploy = deploy.replace('import logging\nlogger = logging.getLogger(__name__)\n',
                        'import logging\nlogger = logging.getLogger(__name__)\nfrom apps.intelligence.models import AIProviderSettings\n')
open('backend/apps/deployments/tasks_deploy.py', 'w').write(deploy)
