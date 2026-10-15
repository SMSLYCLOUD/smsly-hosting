"""Internal wake-on-request for sablier scale-to-zero.

External traffic wakes via the edge router's sablier chain; internal
(container-DNS) dials bypassed Traefik entirely and mesh (mTLS)
dials can never pass a wake router. Covers: internal router labels,
.internal alias host builder, env overlay rewrite, and mesh-served
sleep exclusion.
"""
from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.services.internal_wake import (
    internal_wake_host,
    rewrite_env_for_internal_wake,
    service_has_mesh_sidecar,
)
from apps.deployments.services.traefik_labels import internal_wake_labels


def _svc(name="svc-a", sablier=True, sid="11111111-1111-1111-1111-111111111111"):
    svc = mock.Mock()
    svc.name = name
    svc.id = sid
    svc.sablier_enabled = sablier
    svc.server = None
    svc.active_target_type = ""
    return svc


class InternalWakeHostTests(SimpleTestCase):
    def test_suffix_and_lower(self):
        self.assertEqual(internal_wake_host("Smsly-CRM-9Motr"), "smsly-crm-9motr.internal")

    def test_blank_safe(self):
        self.assertEqual(internal_wake_host(""), "")
        self.assertEqual(internal_wake_host(None), "")


class InternalWakeLabelsTests(SimpleTestCase):
    def test_router_rule_service_chain(self):
        labels = internal_wake_labels(
            router_name="svc_a", service_ref="svc_a-service",
            internal_host="svc-a.internal", group="svc-a", session="10m",
        )
        self.assertEqual(
            labels["traefik.http.routers.svc_a-internal.rule"],
            "Host(`svc-a.internal`)")
        self.assertEqual(
            labels["traefik.http.routers.svc_a-internal.entrypoints"], "web")
        self.assertEqual(
            labels["traefik.http.routers.svc_a-internal.service"],
            "svc_a-service")
        chain = (
            "traefik.http.middlewares.svc_a-internal-sablier.plugin.sablier."
        )
        self.assertEqual(labels[chain + "group"], "svc-a")
        self.assertEqual(
            labels[chain + "sablierUrl"], "http://sablier:10000")
        self.assertEqual(
            labels["traefik.http.middlewares.svc_a-internal-wakeretry.retry.status"],
            "500-599")

    def test_no_edge_only_middlewares(self):
        labels = internal_wake_labels(
            router_name="r", service_ref="s",
            internal_host="h.internal", group="g",
        )
        blob = "\n".join(f"{k}={v}" for k, v in labels.items())
        self.assertNotIn("crowdsec", blob)
        self.assertNotIn("forward-auth", blob)
        self.assertNotIn("websecure", blob)


class RewriteEnvTests(SimpleTestCase):
    def _siblings(self, *names):
        rows = []
        for i, n in enumerate(names):
            s = _svc(name=n, sid=f"22222222-2222-2222-2222-2222222222{i:02d}")
            rows.append(s)
        return rows

    def test_http_sibling_rewritten_to_internal_80(self):
        caller = _svc(name="caller")
        with mock.patch(
                "apps.deployments.services.internal_wake._local_sablier_siblings",
                return_value={"svc-b"}) as _:
            out = rewrite_env_for_internal_wake(
                caller, {"API": "http://svc-b:8000/v1/x", "OTHER": "1"})
        self.assertEqual(out, {"API": "http://svc-b.internal:80/v1/x"})

    def test_no_port_keeps_default(self):
        caller = _svc(name="caller")
        with mock.patch(
                "apps.deployments.services.internal_wake._local_sablier_siblings",
                return_value={"svc-b"}):
            out = rewrite_env_for_internal_wake(
                caller, {"API": "http://svc-b/v1"})
        self.assertEqual(out, {"API": "http://svc-b.internal/v1"})

    def test_https_nonsibling_self_mesh_untouched(self):
        caller = _svc(name="caller")
        with mock.patch(
                "apps.deployments.services.internal_wake._local_sablier_siblings",
                return_value={"svc-b"}):
            env = {
                "A": "https://svc-b:8000/x",
                "B": "http://other:8000/x",
                "C": "http://caller:8000/x",
                "D": "http://10.100.0.1:21597/x",
                "E": "plain",
            }
            self.assertEqual(rewrite_env_for_internal_wake(caller, env), {})

    def test_bad_input_safe(self):
        self.assertEqual(rewrite_env_for_internal_wake(None, None), {})
        self.assertEqual(rewrite_env_for_internal_wake(_svc(), "nope"), {})


class MeshSidecarTests(SimpleTestCase):
    def test_attached_true(self):
        svc = _svc()
        with mock.patch(
                "apps.mtls.models.MtlsConfig") as mc:
            mc.objects.filter.return_value.exists.return_value = True
            self.assertTrue(service_has_mesh_sidecar(svc))

    def test_detached_and_errors_false(self):
        svc = _svc()
        with mock.patch(
                "apps.mtls.models.MtlsConfig") as mc:
            mc.objects.filter.return_value.exists.return_value = False
            self.assertFalse(service_has_mesh_sidecar(svc))
        with mock.patch(
                "apps.mtls.models.MtlsConfig", new=None):
            self.assertFalse(service_has_mesh_sidecar(_svc()))
        self.assertFalse(service_has_mesh_sidecar(None))
