from django.test import SimpleTestCase

from apps.deployments.services.mtls_integration import merge_docker_volumes


class MtlsVolumeMergeTests(SimpleTestCase):
    def test_mtls_mount_replaces_existing_same_destination(self):
        merged = merge_docker_volumes(
            {"old-volume": {"bind": "/opt/spire/run", "mode": "rw"}},
            {"canonical-spire": {"bind": "/opt/spire/run", "mode": "ro"}},
        )

        self.assertEqual(
            merged,
            {"canonical-spire": {"bind": "/opt/spire/run", "mode": "ro"}},
        )
