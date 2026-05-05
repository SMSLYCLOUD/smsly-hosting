import os
import re

path = '/opt/smsly-hosting/docker-compose.prod.yml'
with open(path, 'r') as f:
    content = f.read()

# Find the backend service block
backend_pattern = r'(  backend:.*?healthcheck:.*?test:.*?-\s*CMD-SHELL\n\s*-\s*)(celery -A config inspect ping --timeout 10 2>/dev/null \| grep -q pong)'
new_content = re.sub(backend_pattern, r'\1curl -f http://localhost:8000/health', content, flags=re.DOTALL)

with open(path, 'w') as f:
    f.write(new_content)
print("Updated backend healthcheck")
