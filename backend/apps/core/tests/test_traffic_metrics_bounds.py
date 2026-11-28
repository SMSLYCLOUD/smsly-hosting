"""Regression tests for bounded access-log tailing and skipping OFFLINE remote nodes."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.core.tasks import metrics, traffic


class TrafficLogBoundsTests(SimpleTestCase):
    def test_read_offset_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "missing.log"
            with mock.patch(
                "apps.core.tasks.traffic._offset_file_for_path",
                return_value=Path(tmp) / "missing.offset",
            ):
                self.assertIsNone(traffic._read_offset(log_path))

    def test_resolve_initial_offset_seeks_near_end_for_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            offset_path = Path(tmp) / "access.offset"
            line_payload = json.dumps({
                "request": {"host": "app.internal", "uri": "/ok", "client_ip": "1.2.3.4"},
                "status": 200,
            })
            # Build a file larger than a tiny patched INITIAL_TAIL_BYTES
            lines = [line_payload + "\n" for _ in range(30)]
            log_path.write_text("".join(lines), encoding="utf-8")
            file_size = log_path.stat().st_size

            seen_hosts: list[str] = []

            def fake_resolve(host):
                seen_hosts.append(host)
                return None

            with (
                mock.patch.object(traffic, "INITIAL_TAIL_BYTES", len(line_payload) * 3),
                mock.patch.object(traffic, "MAX_LINES_PER_RUN", 2),
                mock.patch("apps.core.tasks.traffic._offset_file_for_path", return_value=offset_path),
                mock.patch("apps.core.tasks.traffic._resolve_service_for_host", side_effect=fake_resolve),
                mock.patch("apps.core.tasks.traffic.Path") as mock_path_cls,
            ):
                # Only hijack /var/log/traefik/access.log lookup
                mock_path_cls.return_value = log_path
                traffic._tail_sablier_internal_log(primary_log_path=None)

            # Should have bounded scan to <= MAX_LINES_PER_RUN and persisted non-zero offset near EOF
            self.assertLessEqual(len(seen_hosts), 2)
            saved_offset = int(offset_path.read_text(encoding="utf-8"))
            self.assertGreater(saved_offset, file_size - len(line_payload) * 4)
            self.assertLessEqual(saved_offset, file_size)

    def test_collect_traefik_logs_stops_at_max_lines_per_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "caddy-access.log"
            offset_path = Path(tmp) / "caddy.offset"
            entry = json.dumps({
                "request": {"host": "svc.example.com", "uri": "/hello", "client_ip": "8.8.8.8"},
                "status": 200,
            })
            log_path.write_text((entry + "\n") * 10, encoding="utf-8")

            recorded: list[tuple[str, str]] = []
            with (
                mock.patch.object(traffic, "INITIAL_TAIL_BYTES", 1_000_000),
                mock.patch.object(traffic, "MAX_LINES_PER_RUN", 3),
                mock.patch("apps.core.tasks.traffic._is_traffic_geo_enabled", return_value=False),
                mock.patch("apps.core.tasks.traffic._get_active_access_log", return_value=log_path),
                mock.patch("apps.core.tasks.traffic._offset_file_for_path", return_value=offset_path),
                mock.patch("apps.core.tasks.traffic._tail_sablier_internal_log"),
                mock.patch("apps.core.tasks.traffic._upsert_traffic_row", side_effect=lambda ip, host, **_: recorded.append((ip, host))),
            ):
                traffic.collect_traefik_logs.run()

            self.assertEqual(len(recorded), 3)
            self.assertEqual(int(offset_path.read_text(encoding="utf-8")), (len(entry) + 1) * 3)

    def test_collect_remote_traffic_excludes_offline_servers(self):
        fake_qs = mock.MagicMock()
        fake_qs.exclude.return_value = fake_qs
        fake_qs.only.return_value = []
        with mock.patch("apps.deployments.models.servers.ManagedServer.objects.filter", return_value=fake_qs):
            traffic.collect_remote_traffic()
        fake_qs.exclude.assert_called_once_with(status="OFFLINE")


class MetricsOfflineSkipTests(SimpleTestCase):
    def test_collect_remote_container_stats_skips_offline_server(self):
        offline_server = SimpleNamespace(status="OFFLINE")
        service = SimpleNamespace(name="svc", cpu_cores=1, memory_mb=512)
        with (
            mock.patch(
                "apps.deployments.utils.target.resolve_active_execution_target",
                return_value={"target_type": "remote", "server_obj": offline_server},
            ),
            mock.patch("apps.deployments.services.remote_orchestrator.RemoteOrchestrator") as mock_orch,
        ):
            result = metrics._collect_remote_container_stats(service, SimpleNamespace())
        self.assertIsNone(result)
        mock_orch.assert_not_called()
