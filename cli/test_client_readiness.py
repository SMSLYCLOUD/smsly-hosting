"""Exercise real requests serialization without opening a network connection."""
import importlib.util
import io
import json
import tempfile
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

    def test_sk_and_smsly_tokens_use_bearer_auth(self):
        jwt_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"
        for token in ("sk_live_abc123", "smsly_tok_abc123", jwt_token):
            with patch.object(cli, "load_config", return_value={"url": "https://example.invalid", "token": token}):
                request = self.capture("GET", "/api/v1/services/")
            self.assertEqual(request.headers["Authorization"], f"Bearer {token}")

    def test_cmd_deploy_posts_to_service_deploy_endpoint(self):
        response = requests.Response()
        response.status_code = 202
        response._content = b'{"deployment_id":"dep-456"}'
        output = io.StringIO()
        with patch.object(cli, "api_request", return_value=response) as req, redirect_stdout(output):
            cli.cmd_deploy(SimpleNamespace(service_id="svc-123", ref="main"))
        req.assert_called_once_with("POST", "/api/v1/services/svc-123/deploy/", data={"ref": "main"})
        self.assertIn("dep-456", output.getvalue())

    def test_api_request_exits_nonzero_on_request_exception(self):
        with patch.object(requests, "request", side_effect=requests.Timeout("timed out")):
            with self.assertRaises(SystemExit) as ctx:
                cli.api_request("GET", "/api/v1/services/")
        self.assertEqual(ctx.exception.code, 1)

    def test_validate_resource_id_allows_safe_ids_and_rejects_traversal(self):
        self.assertEqual(cli._validate_resource_id("svc_123-abc", "service_id"), "svc_123-abc")
        for bad in ("../etc/passwd", "svc/123", "svc 123", ""):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                cli._validate_resource_id(bad, "service_id")
            self.assertEqual(ctx.exception.code, 1)

    def test_url_scheme_guard_and_saved_config_enforcement(self):
        self.assertEqual(cli._normalize_server_url("https://cloud.example.com/"), "https://cloud.example.com")
        self.assertEqual(cli._normalize_server_url("http://localhost:8090/"), "http://localhost:8090")
        self.assertEqual(
            cli._normalize_server_url("http://remote.example.com", allow_insecure_http=True),
            "http://remote.example.com",
        )
        for bad_url in ("http://remote.example.com", "ftp://remote.example.com", "javascript:alert(1)"):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                cli._normalize_server_url(bad_url)
            self.assertEqual(ctx.exception.code, 1)

        self.config.stop()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir) / ".smsly"
            cfg_file = cfg_dir / "config.json"
            with patch.object(cli, "CONFIG_DIR", str(cfg_dir)), patch.object(cli, "CONFIG_FILE", str(cfg_file)):
                with redirect_stdout(io.StringIO()):
                    cli.save_config("http://remote.example.com", "tok", allow_insecure_http=True)
                loaded = cli.load_config()
                self.assertTrue(loaded["allow_insecure_http"])
                self.assertEqual(loaded["url"], "http://remote.example.com")

                # If allow_insecure_http is false on disk, loading must reject non-loopback http://
                cfg_file.write_text(json.dumps({"url": "http://remote.example.com", "token": "tok", "allow_insecure_http": False}))
                with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                    cli.load_config()
                self.assertEqual(ctx.exception.code, 1)

    def test_pagination_next_path_guard(self):
        self.assertEqual(
            cli._validate_next_path("https://example.invalid/api/v1/services/?page=2", base_url="https://example.invalid"),
            "/api/v1/services/?page=2",
        )
        for bad_next in (
            "https://attacker.invalid/api/v1/services/",
            "//attacker.invalid/api/v1/services/",
            "@attacker.invalid/api/v1/services/",
            "/other/endpoint",
        ):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                cli._validate_next_path(bad_next, base_url="https://example.invalid")
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
