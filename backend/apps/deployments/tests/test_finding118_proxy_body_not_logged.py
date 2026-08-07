import inspect

import apps.deployments.views.server.helpers as views_server_helpers
from django.test import SimpleTestCase

from apps.deployments.views.server import ManagedServerViewSet
from apps.deployments.views.server.helpers import _proxy_error_response


class Finding118ProxyBodyNotLoggedTests(SimpleTestCase):
    def test_proxy_method_does_not_emit_unredacted_body_log(self):
        source = inspect.getsource(ManagedServerViewSet.proxy)
        self.assertNotIn("logger.", source)

    def test_proxy_method_serializes_body_only_for_request(self):
        source = inspect.getsource(ManagedServerViewSet.proxy)
        self.assertIn("body_bytes = json_mod.dumps(body, sort_keys=True).encode()", source)
        self.assertIn("data=body_bytes if body is not None else None", source)

    def test_proxy_error_response_does_not_include_raw_body(self):
        source = inspect.getsource(_proxy_error_response)
        self.assertNotIn("body", source)

    def test_redact_transfer_text_imported_in_views_servers(self):
        module_source = inspect.getsource(views_server_helpers)
        self.assertIn("_redact_transfer_text", module_source)
