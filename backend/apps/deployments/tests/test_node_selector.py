import unittest
from unittest.mock import MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))

sys.modules["django"] = MagicMock()
sys.modules["django.db"] = MagicMock()
sys.modules["django.core.exceptions"] = MagicMock()
sys.modules["django.core.validators"] = MagicMock()
sys.modules["django.utils.translation"] = MagicMock()
sys.modules["encrypted_model_fields.fields"] = MagicMock()
sys.modules["apps.deployments.models"] = MagicMock()
sys.modules['django.conf'] = MagicMock()

from apps.deployments.services.node_selector import select_eligible_node

class MockServer:
    def __init__(self, is_control_plane=False, allow=False, status="ONLINE"):
        self.is_control_plane = is_control_plane
        self.allow_user_workloads = allow
        self.status = status

class TestNodeSelector(unittest.TestCase):
    @patch('apps.deployments.services.node_selector.ManagedServer')
    @patch('apps.deployments.services.node_selector.settings')
    def test_select_node(self, mock_settings, mock_model):
        mock_settings.CLOUDNEURON_ALLOW_CONTROL_PLANE_WORKLOADS = False

        s1 = MockServer(is_control_plane=True, allow=False)
        mock_model.objects.filter.return_value = [s1]
        self.assertIsNone(select_eligible_node("user"))

        s2 = MockServer(is_control_plane=False)
        mock_model.objects.filter.return_value = [s1, s2]
        self.assertEqual(select_eligible_node("user"), s2)

if __name__ == '__main__':
    unittest.main()
