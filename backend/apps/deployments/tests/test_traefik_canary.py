"""Unit tests for the Traefik file-provider canary writer (no DB daemon)."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.deployments.services.traefik_manager import canary_file
from apps.deployments.services.traefik_manager.canary_file import (
    build_canary_config,
    canary_file_path,
    remove_canary_file,
    resolve_canary_topology,
    validate_canary_config,
    write_canary_file,
)

LIVE_LABELS = {
    "traefik.http.routers.myapp.rule": "Host(`app.example.com`)",
    "traefik.http.routers.myapp.priority": "100",
    "traefik.http.routers.myapp.entrypoints": "web",
    "traefik.http.routers.myapp.middlewares": "crowdsec-bouncer@docker",
    "traefik.http.services.myapp.loadbalancer.server.port": "8000",
    "traefik.http.services.myapp.loadbalancer.healthcheck.path": "/health",
}
GREEN_LABELS = {
    "traefik.http.routers.myapp-staging.rule": "Host(`staging.example.com`)",
    "traefik.http.services.myapp-staging.loadbalancer.server.port": "8000",
    "traefik.http.services.myapp-staging.loadbalancer.healthcheck.path": "/health",
}


def _container(labels, status="running", health="healthy"):
    c = MagicMock()
    c.attrs = {
        "Config": {"Labels": dict(labels)},
        "State": {"Status": status, "Health": {"Status": health}},
    }
    return c


def _service(**kwargs):
    base = dict(
        id="svc-1", name="myapp", internal_port=8000, public_domain="app.example.com",
        deploy_mode="SINGLE", deploy_strategy="CANARY", canary_percentage=25,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class ResolveTopologyTests(SimpleTestCase):
    def test_mirrors_live_router(self):
        topo = resolve_canary_topology(
            _service(), SimpleNamespace(id="dep-1", commit_hash="abc"),
            _container(LIVE_LABELS), _container(GREEN_LABELS),
        )
        self.assertEqual(topo["live_router"], "myapp")
        self.assertEqual(topo["live_rule"], "Host(`app.example.com`)")
        self.assertEqual(topo["live_priority"], 100)
        self.assertEqual(topo["entrypoints"], ["web"])
        self.assertEqual(topo["middlewares"], ["crowdsec-bouncer@docker"])
        self.assertEqual(topo["live_svc"], "myapp")
        self.assertEqual(topo["green_svc"], "myapp-staging")
        self.assertTrue(topo["healthcheck_both"])

    def test_ai_router_priority_carried(self):
        labels = dict(LIVE_LABELS, **{"traefik.http.routers.myapp.priority": "1000"})
        topo = resolve_canary_topology(
            _service(), SimpleNamespace(id="dep-1", commit_hash="abc"),
            _container(labels), _container(GREEN_LABELS),
        )
        cfg = build_canary_config(topo, 90, 10)
        router = cfg["http"]["routers"]["myapp-canary"]
        self.assertEqual(router["priority"], 1100)

    def test_missing_live_router_raises(self):
        with self.assertRaises(canary_file.CanaryFileError):
            resolve_canary_topology(
                _service(), SimpleNamespace(id="dep-1", commit_hash="abc"),
                _container({}), _container(GREEN_LABELS),
            )


class BuildConfigTests(SimpleTestCase):
    def _topo(self, **kwargs):
        base = dict(
            live_router="myapp", live_rule="Host(`app.example.com`)",
            live_priority=100, entrypoints=["web"],
            middlewares=["crowdsec-bouncer@docker"], tls_resolver="",
            live_svc="myapp", green_svc="myapp-staging", port=8000,
            healthcheck_both=True, deployment_id="dep-1", commit_hash="abc",
        )
        base.update(kwargs)
        return base

    def test_structure(self):
        cfg = build_canary_config(self._topo(), 75, 25)
        router = cfg["http"]["routers"]["myapp-canary"]
        self.assertEqual(router["priority"], 200)
        self.assertEqual(router["service"], "myapp-canary-wrr")
        self.assertEqual(router["middlewares"], ["crowdsec-bouncer@docker"])
        children = cfg["http"]["services"]["myapp-canary-wrr"]["weighted"]["services"]
        self.assertEqual(children[0], {"name": "myapp@docker", "weight": 75})
        self.assertEqual(children[1], {"name": "myapp-staging@docker", "weight": 25})

    def test_healthcheck_only_when_both_children_have_it(self):
        cfg = build_canary_config(self._topo(healthcheck_both=True), 75, 25)
        self.assertIn("healthCheck", cfg["http"]["services"]["myapp-canary-wrr"]["weighted"])
        cfg = build_canary_config(self._topo(healthcheck_both=False), 75, 25)
        self.assertNotIn("healthCheck", cfg["http"]["services"]["myapp-canary-wrr"]["weighted"])

    def test_tls_resolver_mirrored_when_present(self):
        cfg = build_canary_config(self._topo(tls_resolver="letsencrypt"), 75, 25)
        self.assertEqual(
            cfg["http"]["routers"]["myapp-canary"]["tls"], {"certResolver": "letsencrypt"}
        )

    def test_get_only_restricts_rule_to_reads(self):
        cfg = build_canary_config(self._topo(), 75, 25, get_only=True)
        rule = cfg["http"]["routers"]["myapp-canary"]["rule"]
        self.assertEqual(rule, "(Host(`app.example.com`)) && Method(`GET`, `HEAD`)")
        self.assertEqual(validate_canary_config(cfg, live_priority=100), [])
        cfg = build_canary_config(self._topo(), 75, 25, get_only=False)
        self.assertEqual(
            cfg["http"]["routers"]["myapp-canary"]["rule"], "Host(`app.example.com`)"
        )

    def test_sticky_adds_cookie_affinity(self):
        cfg = build_canary_config(self._topo(), 75, 25, sticky=True)
        weighted = cfg["http"]["services"]["myapp-canary-wrr"]["weighted"]
        self.assertEqual(weighted.get("sticky"), {"cookie": {}})
        self.assertEqual(validate_canary_config(cfg, live_priority=100), [])
        cfg = build_canary_config(self._topo(), 75, 25, sticky=False)
        self.assertNotIn("sticky", cfg["http"]["services"]["myapp-canary-wrr"]["weighted"])

    def test_get_only_and_sticky_compose(self):
        cfg = build_canary_config(self._topo(), 75, 25, get_only=True, sticky=True)
        self.assertIn("Method(`GET`, `HEAD`)", cfg["http"]["routers"]["myapp-canary"]["rule"])
        self.assertEqual(
            cfg["http"]["services"]["myapp-canary-wrr"]["weighted"].get("sticky"),
            {"cookie": {}},
        )
        self.assertEqual(validate_canary_config(cfg, live_priority=100), [])


class ValidateConfigTests(SimpleTestCase):
    def _cfg(self, lw=75, sw=25):
        return {
            "http": {
                "routers": {
                    "myapp-canary": {
                        "rule": "Host(`app.example.com`)",
                        "priority": 200,
                        "entryPoints": ["web"],
                        "service": "myapp-canary-wrr",
                    }
                },
                "services": {
                    "myapp-canary-wrr": {
                        "weighted": {
                            "services": [
                                {"name": "myapp@docker", "weight": lw},
                                {"name": "myapp-staging@docker", "weight": sw},
                            ]
                        }
                    }
                },
            }
        }

    def test_valid(self):
        self.assertEqual(validate_canary_config(self._cfg(), live_priority=100), [])

    def test_sum_must_be_100(self):
        self.assertTrue(any("sum to 90" in e for e in validate_canary_config(self._cfg(70, 20))))

    def test_priority_must_exceed_live(self):
        cfg = self._cfg()
        cfg["http"]["routers"]["myapp-canary"]["priority"] = 100
        self.assertTrue(
            any("must exceed" in e for e in validate_canary_config(cfg, live_priority=100))
        )
        # Without live context only structural sanity applies.
        self.assertFalse(
            any("must exceed" in e for e in validate_canary_config(self._cfg()))
        )

    def test_children_must_be_docker_refs(self):
        cfg = self._cfg()
        cfg["http"]["services"]["myapp-canary-wrr"]["weighted"]["services"][1]["name"] = "green"
        self.assertTrue(any("@docker" in e for e in validate_canary_config(cfg)))

    def test_empty_rule_refused(self):
        cfg = self._cfg()
        cfg["http"]["routers"]["myapp-canary"]["rule"] = ""
        self.assertTrue(validate_canary_config(cfg))

    def test_bad_sticky_shape_refused(self):
        cfg = self._cfg()
        cfg["http"]["services"]["myapp-canary-wrr"]["weighted"]["sticky"] = "cookie"
        self.assertTrue(any("sticky" in e for e in validate_canary_config(cfg)))


class WriteRemoveTests(SimpleTestCase):
    def test_write_then_remove_roundtrip(self):
        service = _service()
        deployment = SimpleNamespace(id="dep-1", green_container_id="abc", commit_hash="abc")
        live = _container(LIVE_LABELS)
        green = _container(GREEN_LABELS)
        client = MagicMock()
        client.containers.get.side_effect = lambda ref: live if ref == "myapp" else green
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(canary_file, "TRAEFIK_DYNAMIC_DIR", tmp), \
             patch(
                 "apps.deployments.services.traefik_manager.canary_file._active_staged_deployment",
                 return_value=deployment,
             ), \
             patch(
                 "apps.deployments.services.traefik_manager.canary_file._docker",
                 return_value=client,
             ), \
             patch(
                 "apps.deployments.services.safedeploy.canary_guard.validate_canary_enable",
                 return_value=(True, []),
             ):
            result = write_canary_file(service, 75, 25)
            path = canary_file_path(service)
            self.assertTrue(path.startswith(tmp))
            self.assertTrue(os.path.exists(path))
            self.assertEqual(result["router"], "myapp-canary")
            import yaml

            on_disk = yaml.safe_load(open(path))
            self.assertEqual(validate_canary_config(on_disk), [])
            self.assertTrue(remove_canary_file(service))
            self.assertFalse(os.path.exists(path))
            self.assertFalse(remove_canary_file(service))

    def test_write_refuses_without_staged(self):
        with patch(
            "apps.deployments.services.traefik_manager.canary_file._active_staged_deployment",
            return_value=None,
        ):
            with self.assertRaises(canary_file.CanaryFileError):
                write_canary_file(_service(), 75, 25)

    def test_write_refuses_bad_weights(self):
        with self.assertRaises(canary_file.CanaryFileError):
            write_canary_file(_service(), 70, 20)

    def test_remove_never_raises(self):
        self.assertFalse(remove_canary_file(SimpleNamespace()))
