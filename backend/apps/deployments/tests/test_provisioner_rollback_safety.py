"""Regression tests for provisioner rollback credential safety.

Covers the B4 class of bugs: a failed (re-)provision must never destroy
the LIVE credential set of a currently-online node.

1. Same-run rollback restores snapshotted gateway/node-db credentials
   instead of blanking them.
2. Same-run rollback never DROPs a DB role that preexisted the run.
3. Same-run rollback drops a freshly created role and clears the
   pending-creation marker.
4. The stale sweeper drops a role only when the pending-creation marker
   is present, and never blanks stored credential fields.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.provisioner.core.cleanup import (
    _rollback_stale_provisioning,
)
from apps.deployments.services.provisioner.provisioning_resources import (
    _ProvisioningResources,
)


def _make_server(user, **overrides):
    kwargs = {
        "owner": user,
        "name": "rollback-test-node",
        "host": "10.0.0.200",
        "gateway_secret": "live-gw-secret",
        "node_db_password": "live-db-pw",
    }
    kwargs.update(overrides)
    return ManagedServer.objects.create(**kwargs)


class ProvisionRollbackSafetyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rollback_admin", password="123")

    def test_rollback_restores_snapshot_instead_of_blanking(self):
        srv = _make_server(self.user)
        resources = _ProvisioningResources(srv)
        resources.snapshot_credentials(
            gateway_secret="live-gw-secret",
            node_db_password="live-db-pw",
        )
        # Simulate the run rotating credentials, then failing.
        srv.gateway_secret = "new-gw-secret"
        srv.node_db_password = "new-db-pw"
        srv.save(update_fields=["gateway_secret", "node_db_password", "updated_at"])

        resources.rollback()

        srv.refresh_from_db()
        self.assertEqual(srv.gateway_secret, "live-gw-secret")
        self.assertEqual(srv.node_db_password, "live-db-pw")

    def test_rollback_blanks_when_no_snapshot(self):
        # First-run failure with no snapshot behaves like the old code.
        srv = _make_server(self.user)
        resources = _ProvisioningResources(srv)
        resources.rollback()
        srv.refresh_from_db()
        self.assertEqual(srv.gateway_secret, "")
        self.assertEqual(srv.node_db_password, "")

    def test_rollback_never_drops_preexisting_db_user(self):
        srv = _make_server(self.user)
        resources = _ProvisioningResources(srv)
        resources.snapshot_credentials(gateway_secret="live-gw-secret", node_db_password="live-db-pw")
        resources.track_db_user("node_agent_abc123")
        resources.mark_db_user_preexisting("node_agent_abc123")
        with patch.object(
            _ProvisioningResources, "_drop_db_user"
        ) as mock_drop:
            resources.rollback()
        mock_drop.assert_not_called()
        # Live credentials untouched.
        srv.refresh_from_db()
        self.assertEqual(srv.gateway_secret, "live-gw-secret")

    def test_rollback_drops_fresh_user_and_clears_pending_marker(self):
        srv = _make_server(self.user)
        srv.provider_metadata = {
            "node_db_user": "node_agent_new",
            "node_db_user_pending": True,
        }
        srv.save(update_fields=["provider_metadata", "updated_at"])
        resources = _ProvisioningResources(srv)
        resources.track_db_user("node_agent_new")
        with patch.object(
            _ProvisioningResources, "_drop_db_user"
        ) as mock_drop:
            resources.rollback()
        mock_drop.assert_called_once_with("node_agent_new")
        srv.refresh_from_db()
        self.assertNotIn("node_db_user_pending", srv.provider_metadata or {})

    def test_sweeper_keeps_preexisting_role_and_credentials(self):
        srv = _make_server(self.user)
        srv.provider_metadata = {"node_db_user": "node_agent_live"}
        srv.save(update_fields=["provider_metadata", "updated_at"])
        with patch(
            "apps.deployments.services.provisioner.core.cleanup._drop_db_user"
        ) as mock_drop:
            _rollback_stale_provisioning(srv)
        mock_drop.assert_not_called()
        srv.refresh_from_db()
        self.assertEqual(srv.node_db_password, "live-db-pw")
        self.assertEqual(srv.gateway_secret, "live-gw-secret")

    def test_sweeper_drops_pending_role_but_keeps_record(self):
        srv = _make_server(self.user)
        srv.provider_metadata = {
            "node_db_user": "node_agent_orphan",
            "node_db_user_pending": True,
        }
        srv.save(update_fields=["provider_metadata", "updated_at"])
        with patch(
            "apps.deployments.services.provisioner.core.cleanup._drop_db_user"
        ) as mock_drop:
            _rollback_stale_provisioning(srv)
        mock_drop.assert_called_once_with("node_agent_orphan")
        srv.refresh_from_db()
        # Record kept for retry reuse (retry recreates the role with the
        # stored password); pending marker consumed.
        self.assertEqual(srv.node_db_password, "live-db-pw")
        self.assertNotIn("node_db_user_pending", srv.provider_metadata or {})
        self.assertNotIn("node_db_user", srv.provider_metadata or {})
