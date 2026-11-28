"""Regression tests for Option 1 (Absolute Minimum RAM & CPU) architecture.

Verifies:
  - OpenAppSec Envoy defines both :8081 shadow -> caddy and :8082 inline -> traefik
  - RabbitMQ is optional under compose `amqp` profile; default Celery broker is Redis DB 4
  - celery-worker-autoscaler.sh supports both Redis and RabbitMQ depth inspection
  - smsly-tier.sh allows waking `appsec` when OPENAPPSEC_ENABLED=1 and lib/docker.sh calls resleep
  - ssh_client.py defers paramiko import until connection/key use
"""
import os
import re
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


class TestOption1Diet(unittest.TestCase):
    def test_openappsec_envoy_has_shadow_and_inline_listeners(self):
        envoy = read("infrastructure/openappsec/envoy.yaml")
        self.assertIn("name: shadow_http", envoy)
        self.assertIn("port_value: 8081", envoy)
        self.assertIn("name: inline_waf", envoy)
        self.assertIn("port_value: 8082", envoy)
        self.assertIn("name: traefik_inline", envoy)
        self.assertIn("address: traefik", envoy)
        self.assertIn("upgrade_type: websocket", envoy)

    def test_compose_rabbitmq_is_amqp_profile_and_no_celery_inspect_ping(self):
        for rel in (
            "docker-compose.prod.yml",
            "infrastructure/docker/docker-compose.node.yml",
            "infrastructure/docker/docker-compose.agent-lite.yml",
        ):
            txt = read(rel)
            self.assertNotIn(
                "celery -A config inspect ping",
                txt,
                f"{rel} still spawns inspect ping interpreter in healthcheck",
            )
            rmq_match = re.search(
                r"^  rabbitmq:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
                txt,
                re.M | re.S,
            )
            self.assertIsNotNone(rmq_match, f"rabbitmq service missing in {rel}")
            self.assertIn("amqp", rmq_match.group(1), f"rabbitmq missing amqp profile in {rel}")

    def test_settings_defaults_celery_broker_to_redis_db4(self):
        settings_py = read("backend/config/settings.py")
        self.assertIn('CELERY_BROKER_MODE', settings_py)
        self.assertIn("standalone_url(_REDIS_BASE_URL, 4)", settings_py)
        self.assertIn("'visibility_timeout': 7800", settings_py)
        self.assertIn("CELERY_RESULT_EXPIRES = 3600", settings_py)

    def test_autoscaler_script_supports_redis_and_rabbitmq(self):
        script = read("scripts/celery-worker-autoscaler.sh")
        self.assertIn("_broker_mode", script)
        self.assertIn("_redis_exec LLEN", script)
        self.assertIn("HLEN unacked", script)
        self.assertIn("messages_unacknowledged", script)

    def test_tier_appsec_gate_and_resleep_hook(self):
        tier_sh = read("scripts/smsly-tier.sh")
        self.assertIn("OPENAPPSEC_ENABLED", tier_sh)
        docker_sh = read("lib/docker.sh")
        self.assertIn("smsly-tier.sh", docker_sh)
        self.assertIn("resleep", docker_sh)

    def test_ssh_client_lazy_imports_paramiko(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(REPO_ROOT, "backend")
        code = (
            "import sys; "
            "from apps.deployments.services import ssh_client; "
            "assert 'paramiko' not in sys.modules, 'paramiko eagerly imported'; "
            "assert callable(ssh_client._get_tofu_policy)"
        )
        res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr or res.stdout)
