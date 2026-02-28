with open("backend/apps/deployments/views.py", "r") as f:
    content = f.read()

bad_block = """        try:
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
            from apps.deployments.services.github_webhooks import setup_github_webhook
import threading
from .models import PlatformConfig
            config = PlatformConfig.load()"""

good_block = """        try:
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
            from .models import PlatformConfig
            config = PlatformConfig.load()"""

content = content.replace(bad_block, good_block)

# Put the import at the top of the file
if "from apps.deployments.services.github_webhooks import setup_github_webhook" not in content[:500]:
    content = content.replace("from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField", "from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField\nfrom apps.deployments.services.github_webhooks import setup_github_webhook\nimport threading")


with open("backend/apps/deployments/views.py", "w") as f:
    f.write(content)
