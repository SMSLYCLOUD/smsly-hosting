from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.mcp.tools import _resolve_user, list_services, get_deployment_status
from apps.mcp.views import _describe_tool, _discover_tools

User = get_user_model()


class ResolveUserTests(TestCase):
    def test_resolve_user_returns_none_when_no_args(self):
        result = _resolve_user()
        self.assertIsNone(result)

    def test_resolve_user_by_id(self):
        user = User.objects.create_user(username="mcptest", email="test@example.com", password="pass")
        result = _resolve_user(user_id=str(user.id))
        self.assertEqual(result, user)

    def test_resolve_user_by_email(self):
        user = User.objects.create_user(username="mcptest", email="test@example.com", password="pass")
        result = _resolve_user(user_email="test@example.com")
        self.assertEqual(result, user)

    def test_resolve_user_not_found_raises(self):
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            _resolve_user(user_id="00000000-0000-0000-0000-000000000000")


class ListServicesTests(TestCase):
    def test_list_services_empty(self):
        result = list_services()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)


class ToolDiscoveryTests(TestCase):
    EXPECTED_TOOLS = {
        "list_services", "get_deployment_status", "get_service_logs",
        "get_service_env_vars", "set_service_env_var", "delete_service_env_var",
        "trigger_service_rebuild", "get_error_diagnostics", "list_projects",
        "get_project_services", "bulk_import_env_vars", "list_service_addons",
        "provision_service_addon", "get_exhaustive_deployment_diagnostics",
        "list_managed_servers", "get_server_health", "deploy_from_local_archive",
        "search_services", "get_service_details", "list_service_deployments",
        "cancel_deployment", "retry_deployment", "get_failed_deployments",
        "list_all_addons", "get_addon_details", "get_service_domains",
    }

    def test_all_expected_tools_discovered(self):
        found = _discover_tools()
        self.assertEqual(set(found), self.EXPECTED_TOOLS)

    def test_hidden_params_excluded_from_schema(self):
        for name, func in _discover_tools().items():
            described = _describe_tool(name, func)
            param_names = [p["name"] for p in described["params"]]
            self.assertNotIn("user_id", param_names, name)
            self.assertNotIn("user_email", param_names, name)


class NewToolsEmptyDbTests(TestCase):
    def test_search_services_empty(self):
        from apps.mcp.tools import search_services
        self.assertEqual(search_services("nothing-here"), [])

    def test_failed_deployments_empty(self):
        from apps.mcp.tools import get_failed_deployments
        self.assertEqual(get_failed_deployments(), [])

    def test_list_all_addons_empty(self):
        from apps.mcp.tools import list_all_addons
        self.assertEqual(list_all_addons(), [])

    def test_cancel_unknown_deployment(self):
        from apps.mcp.tools import cancel_deployment
        result = cancel_deployment("00000000-0000-0000-0000-000000000000")
        self.assertIn("error", result)

    def test_retry_unknown_deployment(self):
        from apps.mcp.tools import retry_deployment
        result = retry_deployment("00000000-0000-0000-0000-000000000000")
        self.assertIn("error", result)

    def test_service_domains_unknown_service(self):
        from apps.mcp.tools import get_service_domains
        result = get_service_domains("00000000-0000-0000-0000-000000000000")
        self.assertIn("error", result)

    def test_addon_details_unknown_addon(self):
        from apps.mcp.tools import get_addon_details
        result = get_addon_details("00000000-0000-0000-0000-000000000000")
        self.assertIn("error", result)
