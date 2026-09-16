"""Regression: secrets generation stays coherent across all producers.

Covers the 2026-09 secrets audit:
  - generate_env_secrets.py emits every key installers consume, with
    valid formats (Fernet keys actually decode, hex lengths match).
  - The placeholder-as-value trap never recurs: --shell output must be
    parseable KEY= lines only, no __INSTALL_CRYPTOGRAPHY__.
  - fresh_config.sh's shell filter accepts every generated key (dropped
    keys were silently regenerated, hiding the mismatch).
  - Consumers agree: infisical compose parameterizes its DB host and
    references no phantom scripts; update_post_deploy verifies images
    that actually exist; validators require what the template writes.
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


def shell_env():
    proc = subprocess.run(
        [sys.executable, "scripts/generate_env_secrets.py", "--shell"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


EXPECTED_HEX = {
    "POSTGRES_PASSWORD": 64,
    "REDIS_PASSWORD": 64,
    "RABBITMQ_PASSWORD": 64,
    "GATEWAY_SECRET": 128,
    "GITHUB_WEBHOOK_SECRET": 128,
    "AUTOSCALER_API_TOKEN": 128,
    "FRP_AUTH_TOKEN": 128,
    "PGCAT_ADMIN_PASSWORD": 96,
    "REPLICATION_PASSWORD": 64,
    "SENTINEL_PASSWORD": 64,
    "REGISTRY_HTTP_SECRET": 64,
    "CROWDSEC_BOUNCER_KEY": 64,
    "COSIGN_PASSWORD": 64,
    "PATRONI_SUPERUSER_PASSWORD": 64,
    "CADDY_ASK_SECRET": 128,
}


class TestSecretsGeneration(unittest.TestCase):
    def test_shell_output_is_clean_key_values(self):
        out = shell_env()
        self.assertNotIn("__INSTALL_CRYPTOGRAPHY__", out)
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.assertRegex(line, r"^[A-Z_]+=.+$", f"unparseable line: {line!r}")

    def test_fernet_keys_decode(self):
        from cryptography.fernet import Fernet

        values = {}
        for line in shell_env().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                values[k] = v
        for key in ("FIELD_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY"):
            Fernet(values[key].encode())  # raises on invalid

    def test_hex_lengths(self):
        values = {}
        for line in shell_env().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                values[k] = v
        for key, want_len in EXPECTED_HEX.items():
            self.assertIn(key, values, f"{key} missing from --shell output")
            self.assertRegex(values[key], r"^[0-9a-f]+$", f"{key} not hex")
            self.assertEqual(len(values[key]), want_len, f"{key} length")

    def test_secret_key_and_grafana_formats(self):
        values = {}
        for line in shell_env().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                values[k] = v
        self.assertRegex(values["SECRET_KEY"], r"^[A-Za-z0-9]{50}$")
        self.assertRegex(values["GRAFANA_PASSWORD"], r"^[A-Za-z0-9\-_]{40}$")

    def test_fresh_filter_accepts_every_generated_key(self):
        """fresh_config.sh's case filter must accept all SECRET_DEFINITIONS."""
        gen = read("scripts/generate_env_secrets.py")
        names = re.findall(r'\("([A-Z_]+)",', gen)
        self.assertGreater(len(names), 10)
        fresh = read("lib/fresh_config.sh")
        m = re.search(r"case \"\$_smsly_secrets_line\" in\n(.*?)\n\s+esac", fresh, re.S)
        self.assertIsNotNone(m, "secrets case filter not found")
        filt = m.group(1)
        for name in names:
            self.assertIn(f"{name}=*", filt, f"{name} dropped by fresh_config filter")

    def test_template_writes_validated_keys(self):
        fresh = read("lib/fresh_config.sh")
        for key in ("BACKUP_ENCRYPTION_KEY", "COSIGN_PASSWORD", "GRAFANA_PASSWORD"):
            self.assertIn(f"{key}=", fresh, f"{key} missing from .env template")

    def test_validate_requires_template_keys(self):
        validation = read("lib/platform-validation.sh")
        for key in ("BACKUP_ENCRYPTION_KEY", "GRAFANA_PASSWORD"):
            self.assertIn(f'"{key}"', validation)


class TestSecretsConsumers(unittest.TestCase):
    def test_registry_login_exported_after_generation(self):
        for rel in ("lib/fresh_deploy.sh", "lib/ops_recovery.sh", "lib/update_preflight.sh"):
            content = read(rel)
            self.assertIn(
                "export REGISTRY_USER=",
                content,
                f"{rel}: REGISTRY_* must be exported or docker_login no-ops",
            )

    def test_infisical_db_host_parameterized(self):
        compose = read("infrastructure/docker/docker-compose.infisical.yml")
        self.assertIn("${INFISICAL_DB_HOST:-smsly-postgres-primary}", compose)
        self.assertNotIn("provision_infisical.sh", compose)
        for rel in ("lib/fresh_deploy.sh", "lib/update_rebuild.sh"):
            content = read(rel)
            self.assertIn('export INFISICAL_DB_HOST=', content, f"{rel} must export DB host")
            self.assertIn("haproxy", content, f"{rel} must handle patroni mode")
            self.assertIn('env_set_value "$INSTALL_DIR/.env" "INFISICAL_ENV_FILE"', content)

    def test_harden_infisical_has_no_phantom_refs(self):
        harden = read("lib/harden_infisical.sh")
        # No code may depend on the never-existing lib/infisical.sh or its
        # infisical_bootstrap() entrypoint (prose mentions in comments, and
        # this layer's own _harden_-prefixed wrappers, are fine).
        for line in harden.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn("lib/infisical.sh", line)
            self.assertNotRegex(line, r"(?<!_harden_)infisical_bootstrap")

    def test_cosign_verify_targets_real_images(self):
        post = read("lib/update_post_deploy.sh")
        self.assertNotIn("smsly/backend:latest", post)
        self.assertIn("smsly-hosting-backend:latest", post)


if __name__ == "__main__":
    unittest.main()
