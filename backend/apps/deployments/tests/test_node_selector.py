import unittest
from unittest.mock import MagicMock, patch

from django.test import TestCase


class TestNodeSelectorSafe(TestCase):
    @patch('apps.deployments.services.node_selector.ManagedServer')
    @patch('apps.deployments.services.node_selector.settings')
    def test_select_node(self, mock_settings, mock_model):
        from apps.deployments.services.node_selector import select_eligible_node
        mock_settings.GRID_ALLOW_CONTROL_PLANE_WORKLOADS = False
        mock_model.ProvisionStatus.DONE = "DONE"
        mock_model.NodeType.MEDIA = "MEDIA"

        s1 = MagicMock()
        s1.is_primary = True
        s1.allow_user_workloads = False
        s1.provision_status = "DONE"
        s1.node_type = "COMPUTE"
        s1.services_count = 0
        s1.status = "ONLINE"
        mock_model.objects.filter.return_value = [s1]
        self.assertIsNone(select_eligible_node("user"))

        s2 = MagicMock()
        s2.is_primary = False
        s2.provision_status = "DONE"
        s2.node_type = "COMPUTE"
        s2.services_count = 0
        s2.status = "ONLINE"
        mock_model.objects.filter.return_value = [s1, s2]
        self.assertEqual(select_eligible_node("user"), s2)

if __name__ == '__main__':
    unittest.main()
