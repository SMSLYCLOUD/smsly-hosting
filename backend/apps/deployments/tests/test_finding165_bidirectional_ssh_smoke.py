"""Smoke tests for Finding #165 (``_is_bidirectional_ssh`` not present).

The deep-sweep report flagged ``_is_bidirectional_ssh`` in
``services/transfer_service.py`` as "not reviewed". On inspection
the function does not exist in the module — the transfer service
delegates SSH selection to ``_target_is_local`` (local when the
target IP is 127.0.0.1, localhost, the platform's own server_ip,
or in the 10.100.0.x wireguard mesh) and to ``SSHClient`` for
remote targets. There is no bidirectional-SSH branch to harden
right now, so this file is a 2-test smoke check that the
neighbouring code paths remain importable and the SSH client
exposes a usable ``exec_command`` interface for the canonical
transfer flow.
"""
import inspect

from django.test import SimpleTestCase

from apps.deployments.services import transfer_service
from apps.deployments.services.ssh_client import SSHClient


class Finding165BidirectionalSshSmokeTests(SimpleTestCase):
    def test_transfer_service_module_imports_and_exposes_neighbors(self):
        self.assertTrue(hasattr(transfer_service, "ServerTransferService"))
        self.assertTrue(hasattr(transfer_service, "TRANSFER_LOG_LIMIT"))
        self.assertTrue(
            hasattr(transfer_service.ServerTransferService, "_target_is_local"),
        )
        self.assertTrue(
            hasattr(transfer_service.ServerTransferService, "_log"),
        )
        self.assertTrue(callable(transfer_service.ServerTransferService))

    def test_bidirectional_helper_is_not_present(self):
        self.assertFalse(
            hasattr(transfer_service, "_is_bidirectional_ssh"),
            "_is_bidirectional_ssh is not part of the current "
            "transfer_service; deferred to future hardening.",
        )

    def test_ssh_client_exposes_exec_command(self):
        self.assertTrue(hasattr(SSHClient, "exec_command"))
        sig = inspect.signature(SSHClient.exec_command)
        self.assertGreaterEqual(len(sig.parameters), 1)
