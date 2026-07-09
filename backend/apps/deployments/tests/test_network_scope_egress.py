"""Regression tests: services/network_scope.py apply_egress_restrictions.

Before the fix:
  * Bridge interface was derived from ``network_name[:12]`` — any two
    networks sharing a 12-char prefix produced identical iptables rules
    that applied to both.
  * iptables inserts were in the wrong order: DROP was inserted BEFORE the
    per-CIDR ACCEPTs, so by the time the loop finished the catch-all DROP
    sat above the specific ACCEPTs and shadowed them — but DNS ACCEPT,
    inserted first, was unreachable below DROP.
  * ``subprocess.run(..., capture_output=True)`` silently swallowed
    iptables errors; operators had no signal when rules failed to apply.

After the fix:
  * Bridge interface is resolved from the Docker network's UUID via the
    Docker API, never from the user-supplied name.
  * DROP is inserted first (ends up at bottom of final chain after all
    ACCEPTs are prepended); DNS ACCEPT is inserted last (sits at top of
    chain, never shadowed).
  * iptables stderr is logged on non-zero exit; FileNotFoundError on the
    ``iptables`` binary is logged, not raised.
  * ``0.0.0.0/0`` in the allowlist short-circuits the function (operator
    intent is "allow anywhere" — there is nothing to restrict).
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.deployments.services.network_scope import (
    _get_bridge_interface_name,
    apply_egress_restrictions,
)


def _fake_completed_process(returncode: int = 0, stderr: str = ""):
    cp = MagicMock()
    cp.returncode = returncode
    cp.stderr = stderr
    return cp


class ApplyEgressRestrictionsTests(SimpleTestCase):
    """Behavioural tests for ``apply_egress_restrictions`` with mocked I/O."""

    # ── Short-circuits ──────────────────────────────────────────────────

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_empty_allowlist_does_nothing(self, mock_docker, mock_run):
        apply_egress_restrictions("any-net", [])
        mock_run.assert_not_called()
        mock_docker.assert_not_called()

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_zero_zero_zero_zero_applies_rfc1918_and_metadata_blocks(self, mock_docker, mock_run):
        """0.0.0.0/0 applies RFC1918 and metadata drop rules before returning."""
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process()

        apply_egress_restrictions("any-net", ["0.0.0.0/0"])
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertEqual(len(calls), 7)
        self.assertIn("169.254.169.254/32", calls[5])
        self.assertIn("DROP", calls[5])

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_mixed_with_zero_zero_applies_rfc1918_and_metadata_blocks(self, mock_docker, mock_run):
        """Even if 0.0.0.0/0 is mixed with narrower CIDRs, apply unrestricted egress with RFC1918 and metadata blocks."""
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process()

        apply_egress_restrictions(
            "any-net", ["10.0.0.0/8", "0.0.0.0/0", "192.168.0.0/16"],
        )
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertEqual(len(calls), 7)

    # ── Bridge interface resolution ─────────────────────────────────────

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_bridge_interface_uses_network_uuid_not_name(
        self, mock_docker, mock_run,
    ):
        """Two networks with name[:12] equal must NOT collide — the bridge
        interface is derived from the network Id, not the name."""
        # Same short prefix, different UUIDs.
        fake_net_a = MagicMock()
        fake_net_a.attrs = {"Id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}
        fake_net_b = MagicMock()
        fake_net_b.attrs = {"Id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

        # First call returns net_a, second returns net_b.
        mock_client = MagicMock()
        mock_client.networks.get.side_effect = [fake_net_a, fake_net_b]
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process()

        # Two networks with identical first 12 characters in their name.
        apply_egress_restrictions("smsly-svc-1234567890ab", ["10.0.0.0/8"])
        apply_egress_restrictions("smsly-svc-1234567890cd", ["10.0.0.0/8"])

        # The ``-i`` flag for each call must use the UUID-derived iface,
        # NOT the network_name[:12].
        call_1_args = mock_run.call_args_list[0][0][0]
        call_2_args = mock_run.call_args_list[4][0][0]
        self.assertEqual(call_1_args[call_1_args.index("-i") + 1], "br-aaaaaaaaaaaa")
        self.assertEqual(call_2_args[call_2_args.index("-i") + 1], "br-bbbbbbbbbbbb")
        # And critically, neither call uses the truncated network name.
        for call_args in (call_1_args, call_2_args):
            iface = call_args[call_args.index("-i") + 1]
            self.assertNotIn("smsly-svc", iface)
            self.assertNotIn("1234567890ab", iface)
            self.assertNotIn("1234567890cd", iface)

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_docker_not_found_aborts_cleanly(self, mock_docker, mock_run):
        import docker as _docker

        mock_client = MagicMock()
        mock_client.networks.get.side_effect = _docker.errors.NotFound("nope")
        mock_docker.return_value = mock_client

        apply_egress_restrictions("missing-net", ["10.0.0.0/8"])
        mock_run.assert_not_called()

    # ── iptables insertion order ────────────────────────────────────────

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_insertion_order_is_drop_then_accepts_then_dns(
        self, mock_docker, mock_run,
    ):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "12345678-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process()

        apply_egress_restrictions(
            "test-net", ["10.0.0.0/8", "192.168.0.0/16"],
        )

        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertEqual(len(calls), 5)

        # 1. DROP first
        self.assertIn("DROP", calls[0])
        self.assertNotIn("--dport", calls[0])
        # 2 & 3. RETURN each CIDR
        self.assertIn("RETURN", calls[1])
        self.assertIn("10.0.0.0/8", calls[1])
        self.assertIn("RETURN", calls[2])
        self.assertIn("192.168.0.0/16", calls[2])
        # 4. DROP cloud metadata
        self.assertIn("DROP", calls[3])
        self.assertIn("169.254.169.254/32", calls[3])
        # 5. DNS RETURN last
        self.assertIn("RETURN", calls[4])
        self.assertIn("--dport", calls[4])
        self.assertIn("53", calls[4])

        # No DROP can appear AFTER a DNS RETURN (would shadow it).
        drop_index = next(i for i, c in enumerate(calls) if "DROP" in c and "169.254" not in c)
        dns_index = next(i for i, c in enumerate(calls) if "--dport" in c)
        self.assertLess(drop_index, dns_index)

    # ── Input validation ────────────────────────────────────────────────

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_invalid_cidrs_are_dropped(self, mock_docker, mock_run):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "abcdefab-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process()

        apply_egress_restrictions(
            "test-net", ["not-a-cidr", "10.0.0.0/8", "also-not-a-cidr"],
        )

        # Should issue DROP + RETURN 10.0.0.0/8 + DROP metadata + RETURN DNS — four calls.
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertEqual(len(calls), 4)
        # No rule should target the invalid entries.
        for c in calls:
            self.assertNotIn("not-a-cidr", c)
            self.assertNotIn("also-not-a-cidr", c)

    # ── Error handling ──────────────────────────────────────────────────

    @patch("apps.deployments.services.network_scope.logger")
    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_iptables_failure_is_logged_not_raised(
        self, mock_docker, mock_run, mock_logger,
    ):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process(
            returncode=1, stderr="iptables: Permission denied",
        )

        # Must not raise.
        apply_egress_restrictions("test-net", ["10.0.0.0/8"])

        # Logger.error was called with the stderr.
        self.assertTrue(mock_logger.error.called)

    @patch("apps.deployments.services.network_scope.logger")
    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_iptables_binary_missing_is_logged_not_raised(
        self, mock_docker, mock_run, mock_logger,
    ):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.side_effect = FileNotFoundError("iptables not found")

        # Must not raise — operator gets a log instead of a stack trace.
        apply_egress_restrictions("test-net", ["10.0.0.0/8"])
        self.assertTrue(mock_logger.error.called)


class GetBridgeInterfaceNameTests(SimpleTestCase):
    """Direct tests for the helper that resolves the Docker bridge iface."""

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_returns_br_prefix_of_network_id(self, mock_docker):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "0123456789abcdef0123456789abcdef"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client

        result = _get_bridge_interface_name("any-network-name")
        self.assertEqual(result, "br-0123456789ab")

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_returns_none_when_network_missing(self, mock_docker):
        import docker as _docker

        mock_client = MagicMock()
        mock_client.networks.get.side_effect = _docker.errors.NotFound("x")
        mock_docker.return_value = mock_client

        self.assertIsNone(_get_bridge_interface_name("missing"))

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_returns_none_when_id_attr_absent(self, mock_docker):
        fake_net = MagicMock()
        fake_net.attrs = {}  # No "Id" key
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client

        self.assertIsNone(_get_bridge_interface_name("broken"))

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_unexpected_exception_returns_none(self, mock_docker):
        mock_docker.return_value.networks.get.side_effect = RuntimeError("boom")
        self.assertIsNone(_get_bridge_interface_name("weird"))
