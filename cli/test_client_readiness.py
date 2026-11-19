"""Exercise real requests serialization without opening a network connection."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest.mock import patch

import requests

spec = importlib.util.spec_from_file_location("smsly_cli", Path(__file__).with_name("smsly_cli.py"))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ClientReadinessTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.object(cli, "load_config", return_value={"url": "https://example.invalid", "token": "test-token"})
        self.config.start()
        self.addCleanup(self.config.stop)

    def capture(self, method, path, **kwargs):
        prepared = []
        def send(session, request, **options):
            prepared.append(request)
            response = requests.Response()
            response.status_code = 201
            response._content = b'{}'
            return response
        with patch.object(requests.Session, "send", send):
            cli.api_request(method, path, **kwargs)
        return prepared[0]

    def test_upload_keeps_service_id_in_multipart_body(self):
        request = self.capture("POST", "/api/v1/deployments/upload/", data={"service_id": "service-123"}, files={"file": ("source.zip", io.BytesIO(b"zip-bytes"))})
        self.assertTrue(request.headers["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="service_id"\r\n\r\nservice-123', request.body)
        self.assertIn(b'filename="source.zip"', request.body)
        self.assertIn(b"zip-bytes", request.body)

    def test_env_update_reports_success_and_preserves_secret_classification(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"key":"TOKEN","value":"masked"}'
        output = io.StringIO()
        with patch.object(cli, "load_project_config", return_value={"service_id": "service-123"}), patch.object(cli, "api_request", return_value=response) as request, redirect_stdout(output):
            cli.cmd_env(SimpleNamespace(env_cmd="set", vars=["TOKEN=new-value"]))
        self.assertNotIn("is_secret", request.call_args.kwargs["data"])
        self.assertIn("Set TOKEN", output.getvalue())
        self.assertNotIn("Failed", output.getvalue())

    def test_regular_request_still_sends_json(self):
        request = self.capture("POST", "/api/v1/services/123/env_vars/", data={"key": "EMPTY", "value": ""})
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(request.body), {"key": "EMPTY", "value": ""})


if __name__ == "__main__":
    unittest.main()
