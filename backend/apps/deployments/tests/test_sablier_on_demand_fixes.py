"""Regression tests for Sablier scale-to-zero / on-demand wake fixes."""
from __future__ import annotations

from datetime import timedelta
import json
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.autoscaler.sleeper import (
    _container_recently_started,
    _service_is_idle,
    _sleep_sablier_services,
)
from apps.core.tasks.traffic import (
    SABLIER_ACTIVITY_CACHE_PREFIX,
    _resolve_service_for_host,
    _tail_sablier_internal_log,
    _upsert_traffic_row,
    collect_traefik_logs,
)
from apps.deployments.models import Project, Service
from apps.deployments.models.core import ManagedServer
from apps.deployments.models.traffic import ServiceTrafficLog
from apps.deployments.serializers.service import ServiceSerializer
from apps.deployments.services.internal_wake import service_has_mesh_sidecar
from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin
from apps.deployments.services.service_ha import ServiceHAManager
from apps.deployments.services.spawning_service import _overflow_router_labels
from apps.deployments.services.traefik_labels import (
    generate_traefik_labels,
    normalize_sablier_session,
)
from apps.deployments.services.traefik_manager.canary_file import (
    qualify_docker_middlewares,
    resolve_canary_topology,
)
from apps.deployments.services.traefik_manager.overflow_file import build_overflow_config

User = get_user_model()


class TrafficActivityAndInternalLogTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="sablier-owner", password="pwd")
        self.project = Project.objects.create(name="Sablier Proj", owner=self.user)
        self.service = Service.objects.create(
            name="wake-svc",
            owner=self.user,
            project=self.project,
            public_domain="wake-svc.smsly.cloud",
            sablier_enabled=True,
            sablier_session="10m",
        )

    def tearDown(self):
        cache.clear()

    def test_upsert_traffic_row_updates_last_seen_on_repeat_ip_and_records_cache(self):
        stale = timezone.now() - timedelta(hours=2)
        row = ServiceTrafficLog.objects.create(
            service=self.service,
            ip_address="203.0.113.10",
            domain="wake-svc.smsly.cloud",
            request_count=1,
            geo_resolved=True,
        )
        ServiceTrafficLog.objects.filter(pk=row.pk).update(last_seen=stale)

        _upsert_traffic_row("203.0.113.10", "wake-svc.smsly.cloud")

        row.refresh_from_db()
        self.assertEqual(row.request_count, 2)
        self.assertGreater(row.last_seen, stale)
        self.assertIsNotNone(cache.get(f"{SABLIER_ACTIVITY_CACHE_PREFIX}{self.service.id}"))

    def test_resolve_internal_host_and_tail_internal_log_without_geo_rows(self):
        self.assertEqual(_resolve_service_for_host("wake-svc.internal").id, self.service.id)

        with tempfile.TemporaryDirectory() as tmpdir:
            traefik_log = os.path.join(tmpdir, "access.log")
            with open(traefik_log, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "request": {"host": "wake-svc.internal:80", "uri": "/api/ping"},
                    "status": 200,
                }) + "\n")

            from pathlib import Path
            real_path = Path(traefik_log)
            with mock.patch("apps.core.tasks.traffic.Path") as path_cls:
                def _path_factory(arg):
                    if arg == "/var/log/traefik/access.log":
                        return real_path
                    return Path(arg)

                path_cls.side_effect = _path_factory
                _tail_sablier_internal_log(primary_log_path=Path("/var/log/caddy/access.log"))

            self.assertIsNotNone(cache.get(f"{SABLIER_ACTIVITY_CACHE_PREFIX}{self.service.id}"))
            self.assertEqual(ServiceTrafficLog.objects.filter(service=self.service).count(), 0)

    def test_collect_traefik_logs_tracks_activity_when_geo_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            caddy_log = os.path.join(tmpdir, "caddy.log")
            with open(caddy_log, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "request": {
                        "host": "wake-svc.smsly.cloud",
                        "uri": "/hello",
                        "client_ip": "198.51.100.7",
                    },
                    "status": 200,
                }) + "\n")

            from pathlib import Path
            with mock.patch("apps.core.tasks.traffic._is_traffic_geo_enabled", return_value=False), \
                 mock.patch("apps.core.tasks.traffic._get_active_access_log", return_value=Path(caddy_log)), \
                 mock.patch("apps.core.tasks.traffic._tail_sablier_internal_log"):
                collect_traefik_logs()

            self.assertIsNotNone(cache.get(f"{SABLIER_ACTIVITY_CACHE_PREFIX}{self.service.id}"))
            self.assertEqual(ServiceTrafficLog.objects.filter(service=self.service).count(), 0)


class SleeperGuardAndStackTests(SimpleTestCase):
    def test_service_is_idle_respects_cache_activity(self):
        svc = mock.Mock(id="svc-cache-1")
        with mock.patch("django.core.cache.cache.get", return_value=__import__("time").time()):
            self.assertFalse(_service_is_idle(svc, 20))

    def test_container_recently_started_skips_sleeper(self):
        recent_iso = (timezone.now() - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        ctr = mock.Mock()
        ctr.status = "running"
        ctr.attrs = {
            "State": {"StartedAt": recent_iso},
            "Config": {"Labels": {"sablier.enable": "true", "sablier.group": "recent-app"}},
        }
        self.assertTrue(_container_recently_started(ctr, grace_minutes=20))

        client = mock.Mock()
        client.containers.get.return_value = ctr
        svc = mock.Mock(id="111", sablier_enabled=True, sablier_session="10m", server=None, active_target_type="")
        svc.name = "recent-app"
        with mock.patch("apps.deployments.models.Service") as svc_model, \
             mock.patch("apps.deployments.models.Deployment") as dep_model, \
             mock.patch("apps.autoscaler.sleeper._service_is_idle", return_value=True), \
             mock.patch("apps.deployments.services.internal_wake.service_has_mesh_sidecar", return_value=False):
            svc_model.objects.filter.return_value = [svc]
            dep_model.objects.filter.return_value.exists.return_value = False
            report = _sleep_sablier_services(client)

        self.assertEqual(report["slept"], [])
        self.assertEqual(report["skipped"].get("recent-app"), "recently started/woken")
        ctr.stop.assert_not_called()

    def test_sleeper_discovers_and_stops_compose_stack_by_service_id_label(self):
        old_iso = (timezone.now() - timedelta(hours=3)).isoformat()
        c_web = mock.Mock(status="running", attrs={
            "State": {"StartedAt": old_iso},
            "Config": {"Labels": {"sablier.enable": "true", "sablier.group": "stack-app"}},
        })
        c_web.name = "stack-app-web-1"
        c_worker = mock.Mock(status="running", attrs={
            "State": {"StartedAt": old_iso},
            "Config": {"Labels": {"sablier.enable": "true", "sablier.group": "stack-app"}},
        })
        c_worker.name = "stack-app-worker-1"

        client = mock.Mock()
        client.containers.get.side_effect = Exception("not found by direct name")
        client.containers.list.return_value = [c_web, c_worker]

        svc = mock.Mock(id="uuid-stack", sablier_enabled=True, sablier_session="10m", server=None, active_target_type="")
        svc.name = "stack-app"
        with mock.patch("apps.deployments.models.Service") as svc_model, \
             mock.patch("apps.deployments.models.Deployment") as dep_model, \
             mock.patch("apps.autoscaler.sleeper._service_is_idle", return_value=True), \
             mock.patch("apps.deployments.services.internal_wake.service_has_mesh_sidecar", return_value=False):
            svc_model.objects.filter.return_value = [svc]
            dep_model.objects.filter.return_value.exists.return_value = False
            report = _sleep_sablier_services(client)

        self.assertEqual(report["slept"], ["stack-app"])
        c_web.stop.assert_called_once_with(timeout=30)
        c_worker.stop.assert_called_once_with(timeout=30)


class TraefikFileQualificationAndLabelsTests(SimpleTestCase):
    def test_qualify_docker_middlewares_adds_docker_suffix_once(self):
        out = qualify_docker_middlewares([
            "crowdsec-bouncer",
            "secure-headers-strict@file",
            "myapp-wakeretry",
            "myapp-sablier",
            "myapp-wakeretry@docker",
        ])
        self.assertEqual(
            out,
            [
                "crowdsec-bouncer@docker",
                "secure-headers-strict@file",
                "myapp-wakeretry@docker",
                "myapp-sablier@docker",
            ],
        )

    def test_canary_and_overflow_preserve_qualified_sablier_middlewares(self):
        live = mock.Mock()
        live.attrs = {
            "Config": {
                "Labels": {
                    "traefik.http.routers.app.rule": "Host(`app.example.com`)",
                    "traefik.http.routers.app.service": "app",
                    "traefik.http.routers.app.middlewares": "secure-headers-strict@file,app-wakeretry,app-sablier",
                    "traefik.http.services.app.loadbalancer.server.port": "8000",
                }
            }
        }
        green = mock.Mock()
        green.attrs = {
            "Config": {
                "Labels": {
                    "traefik.http.routers.app-staging.rule": "Host(`canary-app.example.com`)",
                    "traefik.http.routers.app-staging.service": "app-staging",
                    "traefik.http.services.app-staging.loadbalancer.server.port": "8000",
                }
            }
        }
        svc = mock.Mock(name="app", public_domain="app.example.com")
        dep = mock.Mock(id="dep-1")
        topo = resolve_canary_topology(svc, dep, live, green)
        self.assertIn("app-wakeretry@docker", topo["middlewares"])
        self.assertIn("app-sablier@docker", topo["middlewares"])
        self.assertIn("secure-headers-strict@file", topo["middlewares"])

        ov = build_overflow_config(
            {
                "live_router": "app",
                "live_svc": "app",
                "live_rule": "Host(`app.example.com`)",
                "middlewares": ["app-wakeretry", "app-sablier"],
                "entrypoints": ["web"],
            },
            local_weight=1,
            remote_targets=[{"url": "http://10.0.0.2:8081", "weight": 1, "mesh_http": False}],
        )
        mws = ov["http"]["routers"]["app-overflow"]["middlewares"]
        self.assertIn("app-wakeretry@docker", mws)
        self.assertIn("app-sablier@docker", mws)

    def test_normalize_sablier_session(self):
        self.assertEqual(normalize_sablier_session("15", strict=True), "15m")
        self.assertEqual(normalize_sablier_session(" 1H30M ", strict=True), "1h30m")
        for bad in ("5s", "10d", "abc", "0m"):
            with self.assertRaises(ValueError):
                normalize_sablier_session(bad, strict=True)
            self.assertEqual(normalize_sablier_session(bad, default="10m", strict=False), "10m")

    def test_sablier_omits_active_loadbalancer_healthcheck_probes(self):
        svc = mock.Mock(
            sablier_enabled=True,
            sablier_session="10m",
            edge_jwt_required=False,
        )
        with mock.patch(
            "apps.deployments.services.internal_wake.service_has_mesh_sidecar",
            return_value=False,
        ):
            labels = generate_traefik_labels(
                service_name="sleep-hc",
                domain="sleep-hc.example.com",
                internal_port=8000,
                health_check_path="/health",
                service=svc,
            )
            ov_labels = _overflow_router_labels(svc, None, "8000", "net", "sleep-hc")

        self.assertFalse(any(".loadbalancer.healthcheck." in k for k in labels))
        self.assertFalse(any(".loadbalancer.healthcheck." in item for item in ov_labels))
        self.assertTrue(any(item.startswith("sablier.enable=true") for item in ov_labels))
        self.assertTrue(any(item.startswith("traefik.http.routers.sleep-hc-internal.") for item in ov_labels))


class CaddyPreviewRoutingAndPatchSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="patch-owner", password="pwd")
        self.project = Project.objects.create(name="Patch Proj", owner=self.user)
        self.primary = ManagedServer.objects.create(
            name="primary", owner=self.user, host="203.0.113.10", is_primary=True, status="ONLINE",
        )
        self.remote = ManagedServer.objects.create(
            name="worker-1", owner=self.user, host="203.0.113.11", is_primary=False, status="ONLINE",
        )
        self.service = Service.objects.create(
            name="patch-sablier",
            owner=self.user,
            project=self.project,
            server=self.primary,
            public_domain="patch-sablier.smsly.cloud",
            sablier_enabled=False,
            sablier_session="10m",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_serializer_validates_sablier_session(self):
        ser = ServiceSerializer(instance=self.service, data={"sablier_session": "15"}, partial=True)
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data["sablier_session"], "15m")

        bad = ServiceSerializer(instance=self.service, data={"sablier_session": "5s"}, partial=True)
        self.assertFalse(bad.is_valid())
        self.assertIn("sablier_session", bad.errors)

    def test_patch_sablier_off_wakes_stopped_containers_and_syncs_caddy(self):
        self.service.sablier_enabled = True
        self.service.health_status = "unhealthy"
        self.service.save(update_fields=["sablier_enabled", "health_status"])

        fake_ctr = mock.Mock(id="cid-1", status="exited")
        fake_docker = mock.Mock()
        fake_docker.containers.get.return_value = fake_ctr
        fake_docker.containers.list.return_value = []

        with mock.patch("apps.deployments.views.service.core.ServiceViewSet._sync_caddy") as sync_caddy, \
             mock.patch("apps.cloud.docker_client.get_docker_client", return_value=fake_docker):
            resp = self.client.patch(
                f"/api/v1/services/{self.service.id}/",
                {"sablier_enabled": False},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        sync_caddy.assert_called_once()
        fake_ctr.start.assert_called_once()
        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "unknown")

    def test_patch_remote_sablier_syncs_remote_node(self):
        self.service.server = self.remote
        self.service.save(update_fields=["server"])

        fake_orch = mock.Mock()
        fake_orch._search_remote_service.return_value = "remote-uuid-1"
        with mock.patch("apps.deployments.views.service.core.ServiceViewSet._sync_caddy") as sync_caddy, \
             mock.patch("apps.deployments.services.remote_orchestrator.RemoteOrchestrator", return_value=fake_orch):
            resp = self.client.patch(
                f"/api/v1/services/{self.service.id}/",
                {"sablier_enabled": True, "sablier_session": "20m"},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        sync_caddy.assert_called_once()
        fake_orch._search_remote_service.assert_called_once()
        fake_orch._sync_remote_service_config.assert_called_once_with(self.service, "remote-uuid-1")

    def test_local_preview_sablier_routes_via_traefik_not_direct_ip(self):
        from apps.deployments.models.core import PlatformConfig
        from apps.deployments.services.caddy_manager.config_generation import (
            generate_caddyfile,
            generate_node_caddyfile,
        )

        Service.objects.create(
            name="preview-sablier-1",
            owner=self.user,
            project=self.project,
            server=self.primary,
            public_domain="pr-1.smsly.cloud",
            is_preview=True,
            sablier_enabled=True,
            internal_port=8000,
            status=Service.Status.ACTIVE,
        )
        cfg = PlatformConfig(
            domain="smsly.cloud",
            use_ssl=True,
            wildcard_subdomains=True,
            enable_crowdsec_waf=False,
        )
        caddyfile = generate_caddyfile(cfg)
        self.assertIn("pr-1.smsly.cloud", caddyfile)
        idx = caddyfile.index("pr-1.smsly.cloud")
        snippet = caddyfile[idx:idx + 400]
        self.assertIn("reverse_proxy traefik:80", snippet)
        self.assertIn("header_up Host {host}", snippet)
        self.assertNotIn("reverse_proxy preview-sablier-1:8000", snippet)

        self.remote.node_number = 1
        self.remote.save(update_fields=["node_number"])
        self.service.sablier_enabled = True
        self.service.server = self.remote
        self.service.node_url_enabled = True
        self.service.status = Service.Status.ACTIVE
        self.service.save(update_fields=["sablier_enabled", "server", "node_url_enabled", "status"])
        with mock.patch("apps.deployments.models.core.PlatformConfig.load", return_value=cfg):
            node_caddy = generate_node_caddyfile(self.remote)
        self.assertIn("lb_try_duration 60s", node_caddy)

    def test_service_ha_reconcile_skips_sablier_services(self):
        self.service.sablier_enabled = True
        self.service.ha_mode = "local"
        self.service.auto_restart = True
        self.service.status = Service.Status.ACTIVE
        self.service.save(update_fields=["sablier_enabled", "ha_mode", "auto_restart", "status"])

        mgr = ServiceHAManager()
        with mock.patch.object(mgr, "_spawn_local_replica") as spawn_mock, \
             mock.patch.object(mgr, "_count_running_containers", return_value=0):
            res = mgr._reconcile_local_replicas()

        self.assertEqual(res["reconciled"], 0)
        spawn_mock.assert_not_called()


class ComposeAndMeshSidecarTests(SimpleTestCase):
    def test_mesh_sidecar_requires_enabled_true(self):
        svc = mock.Mock(id="svc-mesh-1")
        with mock.patch("apps.mtls.models.MtlsConfig") as mc:
            mc.objects.filter.return_value.exists.return_value = False
            self.assertFalse(service_has_mesh_sidecar(svc))
            mc.objects.filter.assert_called_once_with(
                service_id="svc-mesh-1", enabled=True, sidecar_enabled=True,
            )

    @override_settings()
    def test_compose_traefik_labels_tls_and_co_sleep_stack(self):
        import yaml

        class _DummyCompose(ComposeNetworkingMixin):
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            compose_file = os.path.join(tmpdir, "docker-compose.yml")
            with open(compose_file, "w", encoding="utf-8") as fh:
                yaml.safe_dump({
                    "services": {
                        "web": {"image": "nginx:alpine"},
                        "worker": {"image": "busybox"},
                    }
                }, fh)

            dummy = _DummyCompose()
            dummy.source_dir = tmpdir
            dummy.build_dir = tmpdir
            dummy.service = mock.Mock(
                id="compose-uuid-1",
                project=None,
                team=None,
                organization=None,
                public_domain="my-stack.smsly.cloud",
                custom_domains=[],
                host_aliases=[],
                is_public=True,
                internal_port=3000,
                sablier_enabled=True,
                sablier_session="15m",
                edge_jwt_required=False,
                disable_crowdsec_waf=True,
                compose_file="docker-compose.yml",
                compose_main_service="web",
                memory_mb=512,
                cpu_cores=1.0,
            )
            dummy.service.name = "my-stack"
            dummy.deployment = mock.Mock(id="dep-compose-1")

            pc = mock.Mock(use_ssl=True, enable_crowdsec_waf=False)
            with mock.patch.dict(os.environ, {"TRAEFIK_ENABLE_WEBSECURE": "true"}), \
                 mock.patch.object(dummy, "_resolve_service_network_name", return_value="smsly-net"), \
                 mock.patch("apps.deployments.services.pipeline.compose_networking.PlatformConfig.load", return_value=pc), \
                 mock.patch("apps.deployments.services.pipeline.compose_networking.is_mtls_enabled", return_value=False), \
                 mock.patch("apps.deployments.services.traefik_manager.edge_shared_file.ensure_edge_shared_file"), \
                 mock.patch("apps.deployments.services.internal_wake.service_has_mesh_sidecar", return_value=False), \
                 mock.patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runc"):
                labels = dummy._compose_traefik_labels("my_stack")
                override_path = dummy._write_compose_routing_override("web", "my_stack")

            with open(override_path, "r", encoding="utf-8") as fh:
                override_doc = yaml.safe_load(fh)

        self.assertEqual(labels["smsly.service_id"], "compose-uuid-1")
        self.assertEqual(labels["sablier.enable"], "true")
        self.assertEqual(labels["sablier.group"], "my-stack")
        self.assertIn("my_stack-wakeretry", labels["traefik.http.routers.my_stack.middlewares"])
        self.assertIn("my_stack-sablier", labels["traefik.http.routers.my_stack.middlewares"])
        self.assertIn("traefik.http.routers.my_stack-internal.rule", labels)
        for svc_name in ("web", "worker"):
            svc_labels = override_doc["services"][svc_name]["labels"]
            self.assertEqual(svc_labels["sablier.enable"], "true")
            self.assertEqual(svc_labels["sablier.group"], "my-stack")
            self.assertEqual(svc_labels["smsly.service_id"], "compose-uuid-1")


class SablierLifecyclePass2GapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pass2-owner", password="pwd")
        self.project = Project.objects.create(name="Pass2 Proj", owner=self.user)
        self.primary = ManagedServer.objects.create(
            name="primary-p2", owner=self.user, host="203.0.113.20", is_primary=True, status="ONLINE",
        )
        self.remote = ManagedServer.objects.create(
            name="worker-p2", owner=self.user, host="203.0.113.21", is_primary=False, status="ONLINE", node_number=2,
        )
        self.service = Service.objects.create(
            name="p2-app",
            owner=self.user,
            project=self.project,
            server=self.primary,
            public_domain="p2-app.smsly.cloud",
            sablier_enabled=True,
            sablier_session="10m",
            status=Service.Status.ACTIVE,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_health_monitor_standby_save_valid_and_not_found_not_masked(self):
        from apps.core.services.health_monitor import _check_service_health
        from apps.deployments.models import Deployment

        dep = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            container_id="p2-app",
            commit_hash="abc",
        )
        Deployment.objects.filter(pk=dep.pk).update(created_at=timezone.now() - timedelta(minutes=30))

        with mock.patch(
            "apps.core.services.health_monitor._probe_container_state",
            return_value={"status": "exited", "exit_code": 0, "restart_count": 0, "started_at": ""},
        ):
            _check_service_health(self.service, Deployment)

        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "standby")

        # Missing container ("not-found") must NOT be disguised as sleeping standby
        self.service.health_status = "healthy"
        self.service.save(update_fields=["health_status"])
        with mock.patch(
            "apps.core.services.health_monitor._probe_container_state",
            return_value={"status": "not-found", "exit_code": None, "restart_count": 0, "started_at": ""},
        ), mock.patch("apps.core.services.health_monitor._build_targets", return_value=[]):
            _check_service_health(self.service, Deployment)

        self.service.refresh_from_db()
        self.assertNotEqual(self.service.health_status, "standby")

    def test_private_sablier_service_enables_internal_wake_only(self):
        from apps.cloud.adapters.local import LocalAdapter

        adapter = LocalAdapter.__new__(LocalAdapter)
        with mock.patch("apps.deployments.services.traefik_manager.edge_shared_file.ensure_edge_shared_file"), \
             mock.patch("apps.deployments.services.internal_wake.service_has_mesh_sidecar", return_value=False):
            labels = adapter._get_traefik_labels(
                "p2-app",
                "Host(`p2-app.smsly.cloud`)",
                port="8000",
                is_public=False,
                network_name="smsly-net",
            )

        self.assertEqual(labels["traefik.enable"], "true")
        self.assertEqual(labels["sablier.enable"], "true")
        self.assertIn("traefik.http.routers.p2-app-internal.rule", labels)
        self.assertNotIn("traefik.http.routers.p2-app.rule", labels)

    def test_stopped_sablier_service_blocked_from_wake_in_sleeper_and_caddy(self):
        from apps.deployments.models.core import PlatformConfig
        from apps.deployments.services.caddy_manager.config_generation import (
            generate_caddyfile,
            generate_node_caddyfile,
        )

        self.service.status = Service.Status.STOPPED
        self.service.save(update_fields=["status"])

        # Sleeper skips STOPPED service
        client = mock.Mock()
        with mock.patch("apps.deployments.models.Service.objects.filter", return_value=[self.service]):
            report = _sleep_sablier_services(client)
        self.assertEqual(report["slept"], [])
        self.assertEqual(report["skipped"].get("p2-app"), "service stopped")

        # Master Caddy routes STOPPED Sablier wildcard host to route-fallback, not traefik
        cfg = PlatformConfig(
            domain="smsly.cloud",
            use_ssl=True,
            wildcard_subdomains=True,
            enable_crowdsec_waf=False,
        )
        master_caddy = generate_caddyfile(cfg)
        self.assertIn("@disabled_wildcard_hosts", master_caddy)
        self.assertIn("p2-app.smsly.cloud", master_caddy.split("@disabled_wildcard_hosts", 1)[1].split("\n")[0])

        # Node Caddy responds 503 instead of proxying to traefik:80
        self.service.server = self.remote
        self.service.node_url_enabled = True
        self.service.save(update_fields=["server", "node_url_enabled"])
        with mock.patch("apps.deployments.models.core.PlatformConfig.load", return_value=cfg):
            node_caddy = generate_node_caddyfile(self.remote)
        self.assertIn('respond "Service is stopped" 503', node_caddy)
        self.assertNotIn("reverse_proxy traefik:80 {", node_caddy)

    def test_remote_internal_wake_alias_and_patch_toggle_on_alias(self):
        from apps.deployments.services.internal_wake import ensure_remote_internal_wake_alias

        ssh = mock.Mock()
        ssh.exec_command.side_effect = [
            (json.dumps({"paas-net": {"Aliases": ["existing.internal"]}}), "", 0),
            ("", "", 0),
        ]
        ok = ensure_remote_internal_wake_alias(ssh, "p2-app", "paas-net")
        self.assertTrue(ok)
        conn_cmd = ssh.exec_command.call_args_list[1][0][0]
        self.assertIn("docker network disconnect -f paas-net", conn_cmd)
        self.assertIn("--alias existing.internal", conn_cmd)
        self.assertIn("--alias p2-app.internal", conn_cmd)

        # Turning Sablier ON via PATCH attaches .internal alias on local container bridges
        self.service.sablier_enabled = False
        self.service.save(update_fields=["sablier_enabled"])
        fake_ctr = mock.Mock(attrs={"NetworkSettings": {"Networks": {"paas-svc-1": {}, "bridge": {}}}})
        fake_docker = mock.Mock()
        fake_docker.containers.get.return_value = fake_ctr
        with mock.patch("apps.deployments.views.service.core.ServiceViewSet._sync_caddy"), \
             mock.patch("apps.cloud.docker_client.get_docker_client", return_value=fake_docker), \
             mock.patch("apps.deployments.services.internal_wake.ensure_internal_wake_alias") as alias_mock, \
             mock.patch("apps.deployments.tasks.refresh_service_sablier_task.delay") as refresh_mock:
            resp = self.client.patch(
                f"/api/v1/services/{self.service.id}/",
                {"sablier_enabled": True},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        alias_mock.assert_called_once_with("p2-app", "paas-svc-1", client=fake_docker)
        refresh_mock.assert_called_once_with(str(self.service.id))

    def test_serializer_rejects_sablier_on_lite_agent(self):
        lite_srv = ManagedServer.objects.create(
            name="lite-p2",
            owner=self.user,
            host="203.0.113.22",
            is_primary=False,
            is_lite_agent=True,
            status="ONLINE",
        )
        lite_svc = Service.objects.create(
            name="lite-sablier-app",
            owner=self.user,
            project=self.project,
            server=lite_srv,
            public_domain="lite.smsly.cloud",
            sablier_enabled=False,
            status=Service.Status.ACTIVE,
        )
        resp = self.client.patch(
            f"/api/v1/services/{lite_svc.id}/",
            {"sablier_enabled": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("sablier_enabled", resp.json())

    def test_health_monitor_flags_oom_and_crash_on_sablier(self):
        from apps.core.services.health_monitor import is_sablier_clean_sleep

        self.service.sablier_enabled = True
        self.assertTrue(is_sablier_clean_sleep(
            self.service, {"status": "exited", "exit_code": 0, "oom_killed": False, "sablier_enrolled": True},
        ))
        self.assertTrue(is_sablier_clean_sleep(
            self.service, {"status": "exited", "exit_code": 143, "oom_killed": False, "sablier_enrolled": True},
        ))
        self.assertFalse(is_sablier_clean_sleep(
            self.service, {"status": "exited", "exit_code": 137, "oom_killed": True, "sablier_enrolled": True},
        ))
        self.assertFalse(is_sablier_clean_sleep(
            self.service, {"status": "exited", "exit_code": 1, "oom_killed": False, "sablier_enrolled": True},
        ))
        self.assertFalse(is_sablier_clean_sleep(
            self.service, {"status": "exited", "exit_code": 0, "oom_killed": False, "sablier_enrolled": False},
        ))

    def test_master_caddy_uses_60s_retry_for_sablier_remote_upstreams(self):
        from apps.deployments.services.caddy_manager.config_generation import (
            _append_reverse_proxy,
        )

        sab_lines: list[str] = []
        _append_reverse_proxy(
            sab_lines,
            "https://10.8.0.2:443 https://10.8.0.3:443",
            "p2-app.smsly.cloud",
            sablier_enabled=True,
        )
        sab_txt = "\n".join(sab_lines)
        self.assertIn("lb_try_duration 60s", sab_txt)
        self.assertIn("lb_try_interval 1s", sab_txt)

        plain_lines: list[str] = []
        _append_reverse_proxy(
            plain_lines,
            "https://10.8.0.2:443 https://10.8.0.3:443",
            "p2-app.smsly.cloud",
            sablier_enabled=False,
        )
        plain_txt = "\n".join(plain_lines)
        self.assertIn("lb_try_duration 5s", plain_txt)

    def test_addon_on_demand_stop_and_start_updates_status(self):
        from apps.deployments.models.addons import Addon
        from apps.addons.services.addon_provisioner import addon_container_name

        addon = Addon.objects.create(
            service=self.service,
            project=self.project,
            name="pg-park",
            addon_type=Addon.Type.POSTGRES,
            provision_mode="container",
            status=Addon.Status.ACTIVE,
        )
        expected_name = addon_container_name(addon)
        fake_ctr = mock.Mock(status="running")
        fake_docker = mock.Mock()
        fake_docker.containers.get.return_value = fake_ctr

        with mock.patch("docker.from_env", return_value=fake_docker):
            resp_stop = self.client.post(f"/api/v1/addons/{addon.id}/stop/")
            self.assertEqual(resp_stop.status_code, 200, resp_stop.content)
            fake_docker.containers.get.assert_called_with(expected_name)
            fake_ctr.stop.assert_called_once_with(timeout=30)
            addon.refresh_from_db()
            self.assertEqual(addon.status, Addon.Status.STOPPED)

            fake_ctr.status = "exited"
            resp_start = self.client.post(f"/api/v1/addons/{addon.id}/start/")
            self.assertEqual(resp_start.status_code, 200, resp_start.content)
            fake_ctr.start.assert_called_once()
            addon.refresh_from_db()
            self.assertEqual(addon.status, Addon.Status.ACTIVE)
