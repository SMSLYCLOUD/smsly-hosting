from django.test import SimpleTestCase

from apps.deployments.services import transfer_service


class Finding165BidirectionalSshAbsenceTests(SimpleTestCase):
    def test_function_does_not_exist(self):
        self.assertFalse(
            hasattr(transfer_service, "_is_bidirectional_ssh"),
            "_is_bidirectional_ssh must not be referenced; the threat "
            "model relies on _target_is_local and SSHClient instead.",
        )

    def test_neighbor_helpers_used_instead(self):
        self.assertTrue(
            hasattr(transfer_service.ServerTransferService, "_target_is_local"),
            "TransferService must still expose _target_is_local to "
            "decide between local and remote SSH flows.",
        )
        self.assertFalse(
            hasattr(transfer_service, "_is_bidirectional_ssh"),
            "If _is_bidirectional_ssh re-appears, revisit Finding #165 "
            "and the threat model.",
        )
