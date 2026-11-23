"""Verify docker-labels-exporter.py is bundled inside backend and matches infrastructure."""
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.autoscaler.services.prometheus_targets import (
    _get_exporter_script_content,
    deploy_docker_labels_exporter_on_node,
)


class DockerLabelsExporterBundleTests(SimpleTestCase):
    def test_bundled_exporter_loads_and_matches_canonical(self):
        content = _get_exporter_script_content()
        self.assertIn("docker_container_labels", content)

        for parent in Path(__file__).resolve().parents:
            canonical = parent / "infrastructure" / "monitoring" / "docker-labels-exporter.py"
            if canonical.exists():
                self.assertEqual(
                    content.replace("\r\n", "\n"),
                    canonical.read_text(encoding="utf-8").replace("\r\n", "\n"),
                )
                break

    def test_deploy_checks_compose_managed_container_name(self):
        class DummyServer:
            name = "node-1"
            host = "198.51.100.10"
            ssh_key = "key"
            ssh_user = "root"
            ssh_port = 22
            ssh_password = ""
            ssh_key_passphrase = ""
            wg_address = "10.100.0.4"

        with patch("apps.deployments.services.ssh_client.SSHClient") as mock_ssh_cls:
            cli = mock_ssh_cls.return_value
            cli.exec_command.return_value = ("running\n", "", 0)
            ok = deploy_docker_labels_exporter_on_node(DummyServer(), force=False)

        self.assertTrue(ok)
        inspected_cmd = cli.exec_command.call_args[0][0]
        self.assertIn("smsly-hosting-docker-labels-1", inspected_cmd)
        self.assertIn("smsly-docker-labels", inspected_cmd)
