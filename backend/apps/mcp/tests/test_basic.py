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


class EnsureMcpServerTests(TestCase):
    def _run(self, status_payload, autostart=True, sdk=True):
        from apps.mcp.tasks import ensure_mcp_server_running
        with patch("apps.mcp.services.MCP_AUTOSTART", autostart), \
             patch("apps.mcp.server._MCP_AVAILABLE", sdk), \
             patch("apps.mcp.services.get_status", return_value=status_payload) as mock_status, \
             patch("apps.mcp.services.start") as mock_start:
            return ensure_mcp_server_running.run(), mock_status, mock_start

    def test_missing_container_started(self):
        from apps.mcp.tasks import ensure_mcp_server_running
        with patch("apps.mcp.services.MCP_AUTOSTART", True), \
             patch("apps.mcp.server._MCP_AVAILABLE", True), \
             patch("apps.mcp.services.get_status", return_value={"exists": False, "running": False}), \
             patch("apps.mcp.services.start") as mock_start:
            mock_start.return_value = {"container_id": "abc"}
            res = ensure_mcp_server_running.run()
        self.assertEqual(res, {"status": "started", "container_id": "abc"})
        mock_start.assert_called_once()

    def test_running_left_alone(self):
        res, _, mock_start = self._run({"exists": True, "running": True, "container_id": "abc"})
        self.assertEqual(res["status"], "already_running")
        mock_start.assert_not_called()

    def test_stopped_left_alone(self):
        res, _, mock_start = self._run({"exists": True, "running": False})
        self.assertEqual(res["status"], "stopped_left_alone")
        mock_start.assert_not_called()

    def test_autostart_disabled(self):
        from apps.mcp.tasks import ensure_mcp_server_running
        with patch("apps.mcp.services.MCP_AUTOSTART", False), \
             patch("apps.mcp.services.start") as mock_start:
            res = ensure_mcp_server_running.run()
        self.assertEqual(res["status"], "disabled")
        mock_start.assert_not_called()

    def test_sdk_missing_skipped(self):
        res, _, mock_start = self._run({"exists": False}, sdk=False)
        self.assertEqual(res["status"], "skipped")
        mock_start.assert_not_called()


class McpTokenApiTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        self.user = User.objects.create_user(username="mcp-token-user", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_list_revoke_token(self):
        created = self.client.post("/api/v1/mcp/tokens/", {"name": "ci"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data["token"].startswith("smsly_"))
        token_id = created.data["id"]

        listed = self.client.get("/api/v1/mcp/tokens/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data["tokens"]), 1)
        self.assertNotIn("token", listed.data["tokens"][0])
        self.assertNotIn("token_hash", listed.data["tokens"][0])

        revoked = self.client.delete(f"/api/v1/mcp/tokens/{token_id}/")
        self.assertEqual(revoked.status_code, 200)
        again = self.client.delete(f"/api/v1/mcp/tokens/{token_id}/")
        self.assertEqual(again.status_code, 404)

    def test_token_authenticates_tool_call(self):
        created = self.client.post("/api/v1/mcp/tokens/", {}, format="json")
        raw = created.data["token"]
        from rest_framework.test import APIClient as RawClient
        anon = RawClient()
        resp = anon.get("/api/v1/mcp/tools/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("tools", resp.data)

    def test_other_users_token_invisible(self):
        other = User.objects.create_user(username="mcp-token-other", password="pass")
        from apps.core.models.api_token import APIToken
        APIToken.create_token(other, "theirs")
        listed = self.client.get("/api/v1/mcp/tokens/")
        self.assertEqual(listed.data["tokens"], [])


class SseReachableTests(TestCase):
    def _status(self, urlopen_result=None, urlopen_error=None):
        from apps.mcp.views import McpStatusView
        from rest_framework.test import APIRequestFactory, force_authenticate
        user = User.objects.create_user(username="mcp-sse-user", password="pass")
        factory = APIRequestFactory()
        request = factory.get("/api/v1/mcp/status/")
        force_authenticate(request, user=user)
        importer = __import__("urllib.request", fromlist=["urlopen"])
        with patch("apps.mcp.services.get_status", return_value={"exists": True, "running": True}), \
             patch.object(importer, "urlopen") as mock_urlopen:
            if urlopen_error:
                mock_urlopen.side_effect = urlopen_error
            else:
                mock_urlopen.return_value.__enter__.return_value.status = 200
            response = McpStatusView.as_view()(request)
        return response

    def test_reachable_when_sse_answers(self):
        self.assertTrue(self._status().data["sse_reachable"])

    def test_reachable_on_rebinding_rejection(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "http://smsly-mcp-server:8001/sse", 421,
            "Misdirected Request", {}, None,
        )
        self.assertTrue(self._status(urlopen_error=err).data["sse_reachable"])

    def test_unreachable_on_error(self):
        self.assertFalse(self._status(urlopen_error=Exception("down")).data["sse_reachable"])
