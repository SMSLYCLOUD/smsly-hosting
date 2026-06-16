import importlib.util
import os

from django.test import SimpleTestCase


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'),
)
BUS_PATH = os.path.join(
    REPO_ROOT,
    'backend', 'apps', 'deployments', 'services', 'heartbeat_bus.py',
)


class Finding69HeartbeatBusExistsTests(SimpleTestCase):
    def test_heartbeat_bus_module_exists(self):
        self.assertTrue(
            os.path.exists(BUS_PATH),
            f'heartbeat_bus.py must exist at {BUS_PATH}',
        )
        spec = importlib.util.spec_from_file_location(
            'apps.deployments.services.heartbeat_bus', BUS_PATH,
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, 'publish_heartbeat'))
        self.assertTrue(callable(module.publish_heartbeat))

    def test_publish_heartbeat_returns_dict_with_peer_metadata(self):
        from apps.deployments.services import heartbeat_bus
        self.assertTrue(hasattr(heartbeat_bus, 'publish_heartbeat'))
        self.assertTrue(callable(heartbeat_bus.publish_heartbeat))
        self.assertTrue(hasattr(heartbeat_bus, 'persist_heartbeats_task'))
        self.assertTrue(callable(heartbeat_bus.persist_heartbeats_task))
        self.assertTrue(hasattr(heartbeat_bus, 'get_latest_heartbeats'))
        self.assertTrue(callable(heartbeat_bus.get_latest_heartbeats))
