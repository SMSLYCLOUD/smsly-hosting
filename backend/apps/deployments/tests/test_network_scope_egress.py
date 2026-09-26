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

Shim era (these tests): every firewall call runs through ``_sh`` as a
``docker run --rm`` one-shot (``sh -c <script>`` is the last argv
element — assertions target the scripts, not the docker argv), with an
idempotency pre-read, attempt-first shim selection, and a self-bounding
apk fallback. A missing docker CLI now raises after one clear log line
(silently shipping an unisolated bridge is worse than the exception).

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

from django.test import SimpleTestCase, TestCase

from apps.deployments.services.network_scope import (
    _get_bridge_interface_name,
    _sh,
    _split_domain_entries,
    apply_egress_restrictions,
    ensure_scoped_network,
    refresh_domain_egress,
    resolve_domain_egress,
)


def _fake_completed_process(returncode: int = 0, stderr: str = "", stdout: str = ""):
    cp = MagicMock()
    cp.returncode = returncode
    cp.stderr = stderr
    cp.stdout = stdout
    return cp


def _scripts(mock_run):
    """The iptables command strings of each docker-run shim invocation.

    Since the shim refactor every firewall call is ``docker run ... sh
    -c <script>`` — assertions target the trailing script element, not
    the docker argv (which no longer carries -i/DROP itself).
    """
    return [c[0][0][-1] for c in mock_run.call_args_list]


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
        scripts = _scripts(mock_run)
        # 1 list + DROP + cross DROP + same RETURN + 5 NIC RETURNs
        # + ESTABLISHED RETURN + metadata DROP + DNS RETURN = 12.
        self.assertEqual(len(scripts), 12)
        self.assertTrue(scripts[0].startswith("iptables -S"))
        self.assertIn("DROP", scripts[-2])
        self.assertIn("169.254.169.254/32", scripts[-2])
        self.assertIn("RETURN", scripts[-1])
        self.assertIn("--dport", scripts[-1])

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
        scripts = _scripts(mock_run)
        self.assertEqual(len(scripts), 12)

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
        # NOT the network_name[:12]. Scripts (not docker argv) carry it.
        scripts = _scripts(mock_run)
        joined = "\n".join(scripts)
        self.assertIn("br-aaaaaaaaaaaa", joined)
        self.assertIn("br-bbbbbbbbbbbb", joined)
        # And critically, neither call uses the truncated network name.
        self.assertNotIn("smsly-svc", joined)
        self.assertNotIn("1234567890ab", joined)
        self.assertNotIn("1234567890cd", joined)

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

        scripts = _scripts(mock_run)
        # 1 list + DROP + 2 CIDR RETURNs + metadata DROP + same-bridge
        # RETURN + ESTABLISHED RETURN + DNS RETURN.
        self.assertEqual(len(scripts), 8)

        # 1. DROP first
        self.assertIn("DROP", scripts[1])
        self.assertNotIn("--dport", scripts[1])
        # 2 & 3. RETURN each CIDR
        self.assertIn("RETURN", scripts[2])
        self.assertIn("10.0.0.0/8", scripts[2])
        self.assertIn("RETURN", scripts[3])
        self.assertIn("192.168.0.0/16", scripts[3])
        # 4. Same-bridge RETURN (addon traffic without Docker chains)
        self.assertIn("RETURN", scripts[4])
        # 5. ESTABLISHED,RELATED RETURN (reply packets)
        self.assertIn("RETURN", scripts[5])
        self.assertIn("ESTABLISHED", scripts[5])
        # 6. DROP cloud metadata
        self.assertIn("DROP", scripts[6])
        self.assertIn("169.254.169.254/32", scripts[6])
        # 7. DNS RETURN last
        self.assertIn("RETURN", scripts[7])
        self.assertIn("--dport", scripts[7])
        self.assertIn("53", scripts[7])

        # No DROP can appear AFTER a DNS RETURN (would shadow it).
        drop_index = next(i for i, s in enumerate(scripts) if "DROP" in s and "169.254" not in s)
        dns_index = next(i for i, s in enumerate(scripts) if "--dport" in s)
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

        # Should issue list + DROP + RETURN 10.0.0.0/8 + DROP metadata
        # + same-bridge RETURN + ESTABLISHED RETURN + RETURN DNS
        # — seven shim calls.
        scripts = _scripts(mock_run)
        self.assertEqual(len(scripts), 7)
        # No rule should target the invalid entries.
        for script in scripts:
            self.assertNotIn("not-a-cidr", script)
            self.assertNotIn("also-not-a-cidr", script)

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
    def test_iptables_binary_missing_raises_after_logging(
        self, mock_docker, mock_run, mock_logger,
    ):
        # Contract change (shim era): a missing docker CLI means scoping
        # is impossible — fail loudly after one clear log line. Silently
        # shipping a bridge with NO egress isolation is worse than an
        # exception the caller (reconcile) already handles.
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.side_effect = FileNotFoundError("iptables not found")

        with self.assertRaises(FileNotFoundError):
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


class ShimFallbackLifetimeTests(SimpleTestCase):
    """Attempt-first shim selection with a self-bounding apk fallback.

    Image *checks* do not survive filtered Docker proxies (IMAGES=0
    answers 404 for images that exist), so ``_sh`` runs the shim and
    only treats create-time pull failures as "missing". And killing the
    docker client on timeout does not stop the container — without an
    inner timeout a dead mirror leaves one ~4-minute zombie process per
    call (observed live: stuck `apk add` containers).
    """

    @patch("apps.deployments.services.network_scope.subprocess.run")
    def test_shim_result_returned_directly(self, mock_run):
        mock_run.return_value = _fake_completed_process(0)
        result = _sh(["iptables", "-L", "DOCKER-USER", "-n"])
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(result.returncode, 0)
        self.assertIn("smsly/iptables-shim:latest", " ".join(mock_run.call_args[0][0]))

    @patch("apps.deployments.services.network_scope.subprocess.run")
    def test_inner_iptables_failure_does_not_fall_back(self, mock_run):
        # rc != 0 from INSIDE the container is a real result, not a
        # missing image — a second run must not fire.
        mock_run.return_value = _fake_completed_process(1, "iptables: Bad rule (does a matching rule exist?)")
        result = _sh(["iptables", "-D", "DOCKER-USER", "1"])
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(result.returncode, 1)

    @patch("apps.deployments.services.network_scope.subprocess.run")
    def test_apk_bootstrap_is_self_bounding(self, mock_run):
        mock_run.side_effect = [
            _fake_completed_process(125, "Error response from daemon: No such image: smsly/iptables-shim:latest"),
            _fake_completed_process(0),
        ]
        result = _sh(["iptables", "-L", "DOCKER-USER", "-n"])
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(result.returncode, 0)
        argv = mock_run.call_args[0][0]
        self.assertIn("sh", argv)
        script = argv[-1]
        self.assertIn("timeout 25 apk add", script)
        # Newer alpine defaults to https repos; port 443 to dl-cdn is
        # blackholed on some networks while the port-80 shim works.
        self.assertIn("http://dl-cdn.alpinelinux.org", script)


class EnsureScopedNetworkEdgeTests(SimpleTestCase):
    """ensure_scoped_network must attach the edge proxy to the bridge.

    Live incident 2026-09-17: a service on a fresh scoped bridge
    (smsly-net-891dc691) was healthy but every domain 503'd because
    neither Traefik nor Caddy had joined the bridge. Attachment is
    best-effort (edge may not exist on agent nodes; filtered proxies
    may deny connect) and must never fail the ensure.
    """

    def _mock_client(self, net=None, edge_names=(), connect_error=None):
        import docker as _docker

        mock_client = MagicMock()
        if net is None:
            mock_client.networks.get.side_effect = _docker.errors.NotFound("x")
            net = MagicMock()
            net.name = "smsly-net-abc123"
            mock_client.networks.create.return_value = net
        else:
            mock_client.networks.get.return_value = net
        if connect_error is not None:
            net.connect.side_effect = connect_error

        def _get_container(name):
            if name in edge_names:
                return MagicMock(name=f"container-{name}")
            raise _docker.errors.NotFound(name)

        mock_client.containers.get.side_effect = _get_container
        return mock_client, net

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_existing_network_attaches_edge(self, mock_docker):
        net = MagicMock()
        net.name = "smsly-net-abc123"
        mock_client, _ = self._mock_client(net=net, edge_names={"smsly-hosting-traefik-1"})
        mock_docker.return_value = mock_client

        self.assertEqual(ensure_scoped_network({"name": "smsly-net-abc123"}), "smsly-net-abc123")
        mock_client.networks.create.assert_not_called()
        net.connect.assert_called_once()

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_created_network_attaches_edge(self, mock_docker):
        mock_client, net = self._mock_client(edge_names={"traefik"})
        mock_docker.return_value = mock_client

        self.assertEqual(ensure_scoped_network({"name": "smsly-net-abc123"}), "smsly-net-abc123")
        mock_client.networks.create.assert_called_once()
        net.connect.assert_called_once()

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_already_attached_is_silent(self, mock_docker):
        import docker as _docker

        net = MagicMock()
        net.name = "smsly-net-abc123"
        mock_client, _ = self._mock_client(
            net=net,
            edge_names={"smsly-hosting-traefik-1"},
            connect_error=_docker.errors.APIError("endpoint already exists in network"),
        )
        mock_docker.return_value = mock_client

        self.assertEqual(ensure_scoped_network({"name": "smsly-net-abc123"}), "smsly-net-abc123")

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_no_edge_present_still_returns_name(self, mock_docker):
        import docker as _docker

        net = MagicMock()
        net.name = "smsly-net-abc123"
        mock_client = MagicMock()
        mock_client.networks.get.return_value = net
        mock_client.containers.get.side_effect = _docker.errors.NotFound("nope")
        mock_docker.return_value = mock_client

        self.assertEqual(ensure_scoped_network({"name": "smsly-net-abc123"}), "smsly-net-abc123")
        net.connect.assert_not_called()


class ScopedNetworkViewsetReconcileTests(TestCase):
    """Scope edits must converge host firewall state, not just the row.

    Before the fix, narrowing allowed_egress_networks only ADDed rules
    (apply is additive with an idempotency gate), so a UI lockdown
    reported restricted while stale RETURNs stayed live.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="scope_admin", password="123", is_staff=True,
            is_superuser=True,
        )
        from apps.deployments.models.core import Project
        self.project = Project.objects.create(name="Scope Proj", owner=self.admin)
        from django.contrib.contenttypes.models import ContentType
        self.project_ct = ContentType.objects.get_for_model(Project)
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def _create_scope(self, egress):
        from apps.deployments.models.network_scope import ScopedNetwork
        return ScopedNetwork.objects.create(
            content_type=self.project_ct,
            object_id=self.project.id,
            network_name="test-br-reconcile",
            allowed_egress_networks=egress,
        )

    def test_update_narrowing_clears_then_reapplies(self):
        from unittest.mock import call
        row = self._create_scope(["0.0.0.0/0"])
        parent = MagicMock()
        with patch(
            "apps.deployments.services.network_scope.clear_scoped_rules",
            parent.clear,
        ), patch(
            "apps.deployments.services.network_scope.apply_egress_restrictions",
            parent.apply,
        ):
            resp = self.client.patch(
                f"/api/v1/network-scopes/{row.id}/",
                {"allowed_egress_networks": ["10.0.0.0/8"]},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        parent.assert_has_calls([
            call.clear("test-br-reconcile"),
            call.apply("test-br-reconcile", ["10.0.0.0/8"]),
        ])

    def test_create_applies_rules(self):
        with patch(
            "apps.deployments.services.network_scope.clear_scoped_rules",
        ) as mock_clear, patch(
            "apps.deployments.services.network_scope.apply_egress_restrictions",
        ) as mock_apply:
            resp = self.client.post(
                "/api/v1/network-scopes/",
                {
                    "scope_type_input": "project",
                    "scope_id": str(self.project.id),
                    "content_type": self.project_ct.id,
                    "object_id": str(self.project.id),
                    "network_name": "test-br-created",
                    "allowed_egress_networks": ["10.0.0.0/8"],
                },
                format="json",
            )
        self.assertEqual(resp.status_code, 201)
        mock_clear.assert_called_once_with("test-br-created")
        mock_apply.assert_called_once_with("test-br-created", ["10.0.0.0/8"])


class DomainEgressTests(SimpleTestCase):
    """domain: allowlist entries resolve to IPs at apply, refresh on beat."""

    def test_split_domain_entries(self):
        cidrs, domains, invalid = _split_domain_entries([
            "10.0.0.0/8",
            "domain:api.resend.com",
            "DOMAIN:Example.COM.",
            "not a cidr",
            "",
        ])
        # Split is syntactic: non-domain text stays in cidrs for the
        # apply step to validate; only malformed domain: entries land
        # in invalid.
        self.assertEqual(cidrs, ["10.0.0.0/8", "not a cidr"])
        self.assertEqual(domains, ["api.resend.com", "example.com"])
        self.assertEqual(invalid, [])

    def test_split_rejects_bad_hostnames(self):
        cidrs, domains, invalid = _split_domain_entries([
            "domain:bad host!",
            "domain:singlelabel",
            "domain:-lead.com",
        ])
        self.assertEqual(cidrs, [])
        self.assertEqual(domains, [])
        self.assertEqual(len(invalid), 3)

    def test_resolve_domain_egress_failure_returns_empty(self):
        with patch("socket.getaddrinfo", side_effect=OSError("nope")):
            self.assertEqual(resolve_domain_egress("example.com"), [])

    def test_resolve_domain_egress_collects_ipv4(self):
        fake = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (10, 1, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake):
            self.assertEqual(
                resolve_domain_egress("example.com"), ["93.184.216.34/32"],
            )

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_apply_resolves_domains_into_rules(self, mock_docker, mock_run):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        mock_run.return_value = _fake_completed_process()
        with patch(
            "apps.deployments.services.network_scope.resolve_domain_egress",
            return_value=["1.2.3.4/32"],
        ):
            apply_egress_restrictions("test-net", ["domain:api.example.com"])
        scripts = _scripts(mock_run)
        joined = "\n".join(scripts)
        self.assertIn("1.2.3.4/32", joined)

    @patch("apps.deployments.services.network_scope.subprocess.run")
    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_apply_all_unresolvable_leaves_bridge_alone(self, mock_docker, mock_run):
        fake_net = MagicMock()
        fake_net.attrs = {"Id": "deadbeef-1234-1234-1234-123456789012"}
        mock_client = MagicMock()
        mock_client.networks.get.return_value = fake_net
        mock_docker.return_value = mock_client
        with patch(
            "apps.deployments.services.network_scope.resolve_domain_egress",
            return_value=[],
        ):
            apply_egress_restrictions("test-net", ["domain:gone.example"])
        # No rule writes at all (returns before even the idempotency
        # pre-read) — so a lockdown claim can never silently mean
        # "wide open".
        scripts = _scripts(mock_run)
        self.assertEqual(scripts, [])


class RefreshDomainEgressTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="domain_scope_admin", password="123", is_staff=True,
            is_superuser=True,
        )
        from apps.deployments.models.core import Project
        self.project = Project.objects.create(name="Domain Proj", owner=self.admin)
        from django.contrib.contenttypes.models import ContentType
        from apps.deployments.models.network_scope import ScopedNetwork
        self.row = ScopedNetwork.objects.create(
            content_type=ContentType.objects.get_for_model(Project),
            object_id=self.project.id,
            network_name="test-br-domains",
            allowed_egress_networks=["domain:api.example.com"],
        )

    def test_refresh_adds_new_and_removes_stale(self):
        from apps.deployments.services import network_scope as ns_mod
        installed = [
            "-i br-test -d 9.9.9.9/32 -j RETURN -m comment --comment smsly-egress-test ",
        ]
        with patch.object(
            ns_mod, "_list_docker_user_rules", return_value=installed,
        ), patch.object(
            ns_mod, "_get_bridge_interface_name", return_value="br-test",
        ), patch.object(
            ns_mod, "resolve_domain_egress", return_value=["1.2.3.4/32"],
        ), patch.object(
            ns_mod, "_sh", return_value=_fake_completed_process(),
        ) as mock_sh:
            stats = refresh_domain_egress()
        self.assertEqual(stats["bridges"], 1)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["removed"], 1)
        calls = [" ".join(c.args[0]) for c in mock_sh.call_args_list]
        self.assertTrue(any("-I" in c and "1.2.3.4/32" in c for c in calls))
        self.assertTrue(any("-D" in c and "9.9.9.9/32" in c for c in calls))

    def test_refresh_keeps_static_cidrs(self):
        from apps.deployments.models.network_scope import ScopedNetwork
        from apps.deployments.services import network_scope as ns_mod
        self.row.allowed_egress_networks = ["10.0.0.0/8", "domain:api.example.com"]
        self.row.save(update_fields=["allowed_egress_networks"])
        installed = [
            "-i br-test -d 10.0.0.0/8 -j RETURN -m comment --comment smsly-egress-test ",
            "-i br-test -d 9.9.9.9/32 -j RETURN -m comment --comment smsly-egress-test ",
        ]
        with patch.object(
            ns_mod, "_list_docker_user_rules", return_value=installed,
        ), patch.object(
            ns_mod, "_get_bridge_interface_name", return_value="br-test",
        ), patch.object(
            ns_mod, "resolve_domain_egress", return_value=["1.2.3.4/32"],
        ), patch.object(
            ns_mod, "_sh", return_value=_fake_completed_process(),
        ) as mock_sh:
            stats = refresh_domain_egress()
        calls = [" ".join(c.args[0]) for c in mock_sh.call_args_list]
        # Static 10.0.0.0/8 untouched; stale 9.9.9.9 removed; new added.
        self.assertFalse(any("10.0.0.0/8" in c for c in calls))
        self.assertTrue(any("-D" in c and "9.9.9.9/32" in c for c in calls))
        self.assertEqual(stats["removed"], 1)
