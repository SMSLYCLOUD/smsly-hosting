with open('backend/apps/deployments/tests/test_core_hardening.py', 'r') as f:
    content = f.read()

# Fix paas_update_blocked_missing_direct_db_url
# The problem is `subprocess.run` actually trying to execute commands, or `os.getenv` mock returning None for everything but DIRECT_DATABASE_URL?
content = content.replace(
    "def test_paas_update_blocked_missing_direct_db_url(self, mock_getenv):",
    "@patch('subprocess.run')\n    def test_paas_update_blocked_missing_direct_db_url(self, mock_subprocess, mock_getenv):"
)
content = content.replace(
    "def test_paas_update_creates_snapshot(self, mock_subprocess, mock_getenv):",
    "@patch('services.platform_updater.CaddyManager.generate_self_signed_cert_if_needed')\n    @patch('services.platform_updater.CaddyManager.update_caddyfile')\n    def test_paas_update_creates_snapshot(self, mock_caddy_update, mock_caddy_cert, mock_subprocess, mock_getenv):"
)
content = content.replace(
    "def mock_env(key, default=None):",
    "def mock_env(key, default=None):\n            if key == 'POSTGRES_PASSWORD': return 'dummy'\n            if key == 'REDIS_PASSWORD': return 'dummy'\n            if key == 'FIELD_ENCRYPTION_KEY': return 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='\n            if key == 'SECRET_KEY': return 'dummy'\n"
)
# Caddy config permission error in test_paas_update_blocked_missing_direct_db_url means we need to mock caddy there too
content = content.replace(
    "@patch('subprocess.run')\n    def test_paas_update_blocked_missing_direct_db_url(self, mock_subprocess, mock_getenv):",
    "@patch('services.platform_updater.CaddyManager.generate_self_signed_cert_if_needed')\n    @patch('services.platform_updater.CaddyManager.update_caddyfile')\n    @patch('subprocess.run')\n    def test_paas_update_blocked_missing_direct_db_url(self, mock_subprocess, mock_caddy_update, mock_caddy_cert, mock_getenv):"
)

with open('backend/apps/deployments/tests/test_core_hardening.py', 'w') as f:
    f.write(content)
