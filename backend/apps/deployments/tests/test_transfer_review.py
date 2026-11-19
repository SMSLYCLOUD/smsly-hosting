"""Transfer deep-review regression tests."""
import re
from types import SimpleNamespace

from django.test import TestCase


def _iptables_chain_order(cmd: str) -> list:
    """Simulate `iptables -I` prepend semantics; return final chain."""
    chain = []
    for line in cmd.splitlines():
        for stmt in line.split(";"):
            stmt = stmt.strip()
            if stmt.startswith("then "):
                stmt = stmt[len("then "):].strip()
            m = re.match(r"iptables -I DOCKER-USER (.+)", stmt)
            if m:
                chain.insert(0, m.group(1).strip())
    return chain


def _classify(rule: str) -> str:
    if "-j DROP" in rule and "-o " not in rule and "-d " not in rule and "--dport" not in rule:
        return "catchall-drop"
    if "10.100.0.0/24" in rule:
        return "mesh-return"
    if "--dport 53" in rule:
        return "dns-return"
    if "-o br-+" in rule:
        return "cross-drop"
    if "-o br-" in rule:
        return "same-return"
    if "-j DROP" in rule:
        return "drop"
    return "return"


class TransferFirewallOrderTests(TestCase):
    def test_catch_all_drop_is_last_and_mesh_allowed(self):
        from apps.deployments.services.transfer_service.mixins.docker import DockerMixin
        service = SimpleNamespace(
            name="fw-test-svc",
            docker_image="nginx:alpine",
            project=None,
            public_domain="fw-test.example.com",
            internal_port=8000,
        )
        cmd = DockerMixin()._generate_docker_run_command(service, {"env_vars": []})
        chain = _iptables_chain_order(cmd)
        kinds = [_classify(rule) for rule in chain]
        # DNS accepts first, catch-all DROP last, mesh allowed.
        self.assertEqual(kinds[0], "dns-return")
        self.assertEqual(kinds[-1], "catchall-drop")
        self.assertIn("mesh-return", kinds)
        self.assertLess(kinds.index("mesh-return"), kinds.index("catchall-drop"))
        self.assertLess(kinds.index("same-return"), kinds.index("catchall-drop"))

    def test_no_shadowed_accepts(self):
        """No ACCEPT/RETURN may appear after (below) the catch-all DROP."""
        from apps.deployments.services.transfer_service.mixins.docker import DockerMixin
        service = SimpleNamespace(
            name="fw-test-svc2",
            docker_image="nginx:alpine",
            project=None,
            public_domain="fw-test2.example.com",
            internal_port=8000,
        )
        cmd = DockerMixin()._generate_docker_run_command(service, {"env_vars": []})
        chain = _iptables_chain_order(cmd)
        kinds = [_classify(rule) for rule in chain]
        drop_idx = kinds.index("catchall-drop")
        for rule in chain[drop_idx + 1:]:
            self.assertNotIn("RETURN", rule)


class TransferOwnerFallbackTests(TestCase):
    def test_no_arbitrary_first_user(self):
        from apps.deployments.services.transfer_service.mixins.service_restore import (
            SingleServiceRestoreMixin,
        )
        script = SingleServiceRestoreMixin._build_restore_trigger_script(
            "owner@example.com", "/tmp/x.tar.gz",
        )
        self.assertNotIn("User.objects.first()", script)
        self.assertIn("is_superuser=True", script)
