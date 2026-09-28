"""Unit tests for scripts/agent_registrar.py mesh fallback.

Regression for 2026-09-28: node heartbeats died at Cloudflare bot-fight
(403) while the /health/live probe (< 500) kept pinning the broken
public URL — the mesh fallback never engaged. Probes now require 2xx,
and IP-literal HTTPS bases go direct over WireGuard with S3NI + Host
of the public master hostname.
"""
import importlib.util
import os
import sys
from unittest import mock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "agent_registrar.py")


def _load(monkeypatch, master_url="https://trulay.site"):
    monkeypatch.setenv("MASTER_API_URL", master_url)
    monkeypatch.setenv("MASTER_API_URL_FALLBACK", "https://10.100.0.1")
    monkeypatch.setenv("SERVER_ID", "srv-1")
    monkeypatch.setenv("GATEWAY_SECRET", "sekret")
    spec = importlib.util.spec_from_file_location("agent_registrar_ut", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_registrar_ut"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_primary_host_derivation(monkeypatch):
    mod = _load(monkeypatch)
    assert mod._primary_host() == "trulay.site"


def test_ip_literal_detection(monkeypatch):
    mod = _load(monkeypatch)
    assert mod._url_host_is_ip("https://10.100.0.1/api/x") is True
    assert mod._url_host_is_ip("https://trulay.site/api/x") is False


def test_probe_rejects_cf_challenge(monkeypatch):
    """A 403 (bot challenge) must NOT pin the URL."""
    mod = _load(monkeypatch)
    resp = mock.MagicMock()
    resp.status = 403
    resp.__enter__.return_value = resp
    with mock.patch.object(mod.urllib.request, "urlopen", return_value=resp):
        assert mod.BaseUrlResolver(["https://trulay.site"]).current() is None


def test_probe_accepts_healthy_master(monkeypatch):
    mod = _load(monkeypatch)
    resp = mock.MagicMock()
    resp.status = 200
    resp.__enter__.return_value = resp
    with mock.patch.object(mod.urllib.request, "urlopen", return_value=resp):
        assert (
            mod.BaseUrlResolver(["https://trulay.site"]).current()
            == "https://trulay.site"
        )


def test_mesh_url_uses_sni_opener_and_host_header(monkeypatch):
    mod = _load(monkeypatch)
    seen = {}

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_build_opener(handler):
        seen["handler"] = type(handler).__name__
        opener = mock.MagicMock()
        opener.open.return_value = FakeResp()
        return opener

    with mock.patch.object(
        mod.urllib.request, "build_opener", side_effect=fake_build_opener
    ), mock.patch.object(mod.urllib.request, "urlopen") as mock_direct:
        status, _ = mod._post_json(
            "https://10.100.0.1", "/api/v1/servers/srv-1/agent-heartbeat/", {}
        )
    assert status == 200
    assert seen.get("handler") == "_SNIHTTPSHandler"
    mock_direct.assert_not_called()


def test_public_url_uses_plain_urlopen(monkeypatch):
    mod = _load(monkeypatch)
    with mock.patch.object(mod.urllib.request, "urlopen") as mock_direct, \
         mock.patch.object(mod.urllib.request, "build_opener") as mock_builder:
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = b"{}"
        resp.__enter__.return_value = resp
        mock_direct.return_value = resp
        status, _ = mod._post_json(
            "https://trulay.site", "/api/v1/servers/srv-1/agent-heartbeat/", {}
        )
    assert status == 200
    mock_builder.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
