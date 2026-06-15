import inspect

from django.test import SimpleTestCase

from apps.deployments import views_servers


class Finding118ProxyBodyNotLoggedTests(SimpleTestCase):
    def test_proxy_method_does_not_emit_unredacted_body_log(self):
        source = inspect.getsource(views_servers.ManagedServerViewSet.proxy)
        self.assertNotIn("logger.", source)

    def test_proxy_method_serializes_body_only_for_request(self):
        source = inspect.getsource(views_servers.ManagedServerViewSet.proxy)
        self.assertIn("body_bytes = json_mod.dumps(body, sort_keys=True).encode()", source)
        self.assertIn("data=body_bytes if body is not None else None", source)

    def test_proxy_error_response_does_not_include_raw_body(self):
        source = inspect.getsource(views_servers._proxy_error_response)
        self.assertNotIn("body", source)

    def test_redact_transfer_text_imported_in_views_servers(self):
        module_source = inspect.getsource(views_servers)
        self.assertIn("_redact_transfer_text", module_source)
