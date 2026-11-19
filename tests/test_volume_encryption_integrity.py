"""Repo-level checks for F10 observability TLS & F11 volume encryption integrity scripts."""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


class TestObservabilityAndIntegrity(unittest.TestCase):
    def test_observability_compose_removes_sslmode_disable(self):
        content = read("infrastructure/docker/docker-compose.observability.yml")
        self.assertNotIn("sslmode=disable", content)
        self.assertIn("@smsly-shared-postgres:5432/postgres?sslmode=require", content)
        self.assertIn("@smsly-postgres-primary:5432/postgres?sslmode=prefer", content)

    def test_integrity_script_guards_encrypted_volumes_and_pooler_tls(self):
        script = read("scripts/verify_platform_integrity.sh")
        self.assertIn("ensure_encrypted_volumes_healthy", script)
        self.assertIn("pgbouncer-tenants", script)
        self.assertIn("/etc/pgbouncer/tls/server.key", script)
        self.assertIn("postgres:600", script)
        self.assertIn("smsly-shared-postgres", script)

        fn_match = re.search(
            r"ensure_encrypted_volumes_healthy\(\)\s*\{(.*?)\n\}",
            script,
            re.S,
        )
        self.assertIsNotNone(fn_match)
        body = fn_match.group(1)
        self.assertIn("timeout -k 5", body)
        self.assertIn("mountpoint -q", body)
        self.assertIn("fscrypt status", body)
        self.assertIn("/data", body)

    def test_volume_crypt_helper_uses_timeouts_and_data_subdir_guard(self):
        helper = read("scripts/smsly-volume-crypt.sh")
        self.assertIn("set -euo pipefail", helper)
        self.assertIn("timeout -k 5", helper)
        self.assertIn("chmod 0000 \"$mnt\"", helper)
        self.assertIn("${mnt}/data", helper)
        self.assertIn("cryptsetup luksFormat", helper)
        self.assertIn("fscrypt encrypt", helper)
        self.assertIn("unlock-all", helper)


if __name__ == "__main__":
    unittest.main()
