# pylint: disable=invalid-name
"""Sidecar hardening: retry, namespace reattach, wait self-heal, mesh prep.

Covers the never-fail-sidecar work:
- inject retries one transient daemon error (not 409 races, not ImageNotFound)
- check_namespace_current / reattach_if_stale across current/stale/missing
- wait_sidecar_ready re-injects a vanished sidecar within the deadline
- _post_deploy_success reattaches after promote (errors suppressed)
- _prepare_ecosystem_mesh fails the PLAN fast on image/template/agent
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.mtls.models import MtlsConfig


def _api_error(status):
    import docker.errors
    resp = MagicMock()
    resp.status_code = status
    resp.reason = f"status {status}"
    resp.url = "http://docker/"
    return docker.errors.APIError(
        f"daemon error {status}", response=resp,
        explanation=f"daemon error {status}")


def _not_found_error():
    import docker.errors
    return docker.errors.NotFound("No such container")


class InjectRetryTests(TestCase):
    def _service(self):
        user = User.objects.create_user(username="harden-user", password="pwd")
        provider = CloudProvider.objects.create(
            name="harden-local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        svc = Service.objects.create(
            name="harden-svc", owner=user, provider=provider,
        )
        config = MtlsConfig.objects.get(service=svc)
        config.enabled = True
        config.sidecar_enabled = True
        config.save(update_fields=["enabled", "sidecar_enabled"])
        return svc

    def _inject(self, svc, run_side_effect):
        from apps.mtls.services import envoy_sidecar as mod
        client = MagicMock()
        client.containers.run.side_effect = run_side_effect
        main = MagicMock()
        main.id = "main123"
        with patch.object(mod.EnvoySidecar, "_find_main_container",
                          return_value=main), \
             patch.object(mod.EnvoySidecar, "ensure_sidecar_image",
                          return_value=mod.ENVOY_IMAGE), \
             patch.object(mod.EnvoySidecar, "generate_config",
                          return_value="cfg"), \
             patch.object(mod.EnvoySidecar, "_remove_legacy_template_poison",
                          return_value=None), \
             patch("apps.cloud.docker_client.get_docker_client",
                   return_value=client), \
             patch("apps.mtls.services.envoy_sidecar.time"):
            return mod.EnvoySidecar.inject_sidecar(svc), client

    def test_retries_transient_error_once(self):
        ok_container = MagicMock()
        ok_container.id = "sidecar999"
        result, client = self._inject(self._service(), [_api_error(500), ok_container])
        self.assertEqual(result["status"], "injected")
        self.assertEqual(client.containers.run.call_count, 2)

    def test_conflict_returns_immediately_without_retry(self):
        result, client = self._inject(self._service(), [_api_error(409)])
        self.assertEqual(result["status"], "already_running")
        self.assertEqual(client.containers.run.call_count, 1)

    def test_image_not_found_not_retried(self):
        import docker.errors
        from apps.mtls.services import envoy_sidecar as mod
        from unittest.mock import MagicMock as _MM, patch as _patch
        err = docker.errors.ImageNotFound("No such image")
        client = _MM()
        client.containers.run.side_effect = [err]
        main = _MM()
        main.id = "main123"
        with _patch.object(mod.EnvoySidecar, "_find_main_container",
                           return_value=main), \
             _patch.object(mod.EnvoySidecar, "ensure_sidecar_image",
                           return_value=mod.ENVOY_IMAGE), \
             _patch.object(mod.EnvoySidecar, "generate_config",
                           return_value="cfg"), \
             _patch.object(mod.EnvoySidecar, "_remove_legacy_template_poison",
                           return_value=None), \
             _patch("apps.cloud.docker_client.get_docker_client",
                    return_value=client), \
             _patch("apps.mtls.services.envoy_sidecar.time"):
            with self.assertRaises(Exception):
                mod.EnvoySidecar.inject_sidecar(self._service())
        self.assertEqual(client.containers.run.call_count, 1)


class NamespaceCheckTests(TestCase):
    def _service(self):
        svc = MagicMock()
        svc.name = "ns-svc"
        return svc

    def _client_with(self, sidecar=None, main_id="liveABCDEF123456"):
        client = MagicMock()
        main = MagicMock()
        main.id = main_id
        client._main = main
        if sidecar is None:
            client.containers.get.side_effect = Exception("No such container")
        else:
            client.containers.get.return_value = sidecar
        return client

    @staticmethod
    def _sidecar(net_mode, status="running"):
        sidecar = MagicMock()
        sidecar.status = status
        sidecar.attrs = {"HostConfig": {"NetworkMode": net_mode}}
        return sidecar

    def test_current_when_ids_match(self):
        from apps.mtls.services import envoy_sidecar as mod
        sidecar = self._sidecar("container:liveABCDEF1234567890")
        client = self._client_with(sidecar)
        with patch("apps.cloud.docker_client.get_docker_client", return_value=client), \
             patch.object(mod.EnvoySidecar, "_find_main_container", return_value=client._main):
            result = mod.EnvoySidecar.check_namespace_current(self._service())
        self.assertTrue(result["current"])
        self.assertFalse(result["stale"])

    def test_stale_when_bound_to_old_container(self):
        from apps.mtls.services import envoy_sidecar as mod
        sidecar = self._sidecar("container:deadBEEF0000000000")
        client = self._client_with(sidecar)
        with patch("apps.cloud.docker_client.get_docker_client", return_value=client), \
             patch.object(mod.EnvoySidecar, "_find_main_container", return_value=client._main):
            result = mod.EnvoySidecar.check_namespace_current(
                self._service(), live_container_id="liveABCDEF1234567890")
        self.assertFalse(result["current"])
        self.assertTrue(result["stale"])

    def test_missing_sidecar_is_not_stale(self):
        from apps.mtls.services import envoy_sidecar as mod
        client = self._client_with(None)
        with patch("apps.cloud.docker_client.get_docker_client", return_value=client), \
             patch.object(mod.EnvoySidecar, "_find_main_container", return_value=client._main):
            result = mod.EnvoySidecar.check_namespace_current(self._service())
        self.assertFalse(result["current"])
        self.assertFalse(result["stale"])

    def test_reattach_removes_and_injects_on_stale(self):
        from apps.mtls.services import envoy_sidecar as mod
        with patch.object(mod.EnvoySidecar, "check_namespace_current",
                          return_value={"current": False, "stale": True, "reason": "x"}), \
             patch.object(mod.EnvoySidecar, "remove_sidecar") as mock_remove, \
             patch.object(mod.EnvoySidecar, "inject_sidecar",
                          return_value={"status": "injected", "name": "envoy-ns-svc"}) as mock_inject:
            result = mod.EnvoySidecar.reattach_if_stale(self._service(), live_container_id="live1")
        mock_remove.assert_called_once()
        mock_inject.assert_called_once()
        self.assertTrue(result["reattached"])

    def test_reattach_noop_when_current(self):
        from apps.mtls.services import envoy_sidecar as mod
        with patch.object(mod.EnvoySidecar, "check_namespace_current",
                          return_value={"current": True, "stale": False}), \
             patch.object(mod.EnvoySidecar, "remove_sidecar") as mock_remove, \
             patch.object(mod.EnvoySidecar, "inject_sidecar") as mock_inject:
            result = mod.EnvoySidecar.reattach_if_stale(self._service(), live_container_id="live1")
        mock_remove.assert_not_called()
        mock_inject.assert_not_called()
        self.assertFalse(result["reattached"])


class WaitSelfHealTests(TestCase):
    def _service(self):
        svc = MagicMock()
        svc.name = "smsly-backend"
        return svc

    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_name",
           return_value="envoy-smsly-backend")
    @patch("apps.cloud.docker_client.get_docker_client")
    @patch("apps.mtls.services.envoy_sidecar.time")
    def test_reinjects_vanished_sidecar(self, mock_time, mock_client_fn, _mock_name):
        from apps.mtls.services import envoy_sidecar as mod
        gone = _not_found_error()
        container = MagicMock()
        container.status = "running"
        ready = MagicMock(exit_code=0, output=b"OK")
        certs = MagicMock(
            exit_code=0,
            output=b'{"certificates": [{"cert_chain": [{"subject_alt_names": '
                   b'[{"uri": "spiffe://ecosystem.local/service/smsly-backend"}]}]}]}',
        )
        container.exec_run.side_effect = [ready, certs]
        mock_client_fn.return_value.containers.get.side_effect = [gone, container]
        mock_time.time.side_effect = [0, 0, 0, 0, 0, 100]
        with patch.object(mod.EnvoySidecar, "inject_sidecar",
                          return_value={"status": "injected"}) as mock_inject:
            self.assertTrue(mod.EnvoySidecar.wait_sidecar_ready(
                self._service(), timeout_seconds=30))
        mock_inject.assert_called_once()


class PromoteHookTests(TestCase):
    def test_post_deploy_success_reattaches_mesh(self):
        from apps.deployments.tasks.deploy import state as state_mod
        user = User.objects.create_user(username="promote-user", password="pwd")
        provider = CloudProvider.objects.create(
            name="promote-local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        svc = Service.objects.create(
            name="promote-svc", owner=user, provider=provider,
        )
        config = MtlsConfig.objects.get(service=svc)
        config.enabled = True
        config.sidecar_enabled = True
        config.save(update_fields=["enabled", "sidecar_enabled"])
        deployment = Deployment.objects.create(
            service=svc, status=Deployment.Status.ACTIVE,
            commit_hash="p1", container_id="live999",
        )
        with patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.reattach_if_stale",
                   return_value={"status": "current", "reattached": False}) as mock_reattach:
            state_mod._post_deploy_success(deployment, svc)
        mock_reattach.assert_called_once()
        self.assertEqual(mock_reattach.call_args[0][0].id, svc.id)

    def test_post_deploy_success_suppresses_reattach_errors(self):
        from apps.deployments.tasks.deploy import state as state_mod
        user = User.objects.create_user(username="promote-user2", password="pwd")
        provider = CloudProvider.objects.create(
            name="promote-local2",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        svc = Service.objects.create(
            name="promote-svc2", owner=user, provider=provider,
        )
        config = MtlsConfig.objects.get(service=svc)
        config.enabled = True
        config.sidecar_enabled = True
        config.save(update_fields=["enabled", "sidecar_enabled"])
        deployment = Deployment.objects.create(
            service=svc, status=Deployment.Status.ACTIVE,
            commit_hash="p1", container_id="live999",
        )
        with patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.reattach_if_stale",
                   side_effect=RuntimeError("daemon down")):
            # Must not raise: promotion already succeeded.
            state_mod._post_deploy_success(deployment, svc)


class MeshPrepTests(TestCase):
    def _entries(self):
        return {
            "a": {"requested_name": "svc-a", "plan": {"internal": True}},
            "b": {"requested_name": "svc-b", "plan": {"internal": False}},
        }

    def _run_prep(self, image_ok=True, agent_ok=True, template_ok=True):
        from apps.deployments.tasks.ecosystem.tasks import _prepare_ecosystem_mesh
        client = MagicMock()
        agent = MagicMock()
        agent.exec_run.return_value = MagicMock(exit_code=0 if agent_ok else 1)
        client.containers.get.return_value = agent
        mock_time = MagicMock()
        mock_time.time.side_effect = [100] * 30
        if image_ok:
            image_patch = patch(
                "apps.mtls.services.envoy_sidecar.EnvoySidecar.ensure_sidecar_image",
                return_value="img")
        else:
            image_patch = patch(
                "apps.mtls.services.envoy_sidecar.EnvoySidecar.ensure_sidecar_image",
                side_effect=Exception("no image"))
        with patch("apps.cloud.docker_client.get_docker_client",
                   return_value=client), \
             patch("os.path.isfile", return_value=template_ok), \
             patch("apps.deployments.tasks_spiffe._create_spire_entry",
                   return_value=True) as mock_entry, \
             patch("apps.deployments.tasks_spiffe._live_ecosystem_agent_id",
                   return_value="spiffe://ecosystem.local/agent/x"), \
             image_patch, \
             patch("apps.deployments.tasks.ecosystem.tasks.time", mock_time):
            return _prepare_ecosystem_mesh(self._entries()), mock_entry

    def test_prep_ok(self):
        err, mock_entry = self._run_prep()
        self.assertIsNone(err)
        # Only the internal service gets a pre-created entry.
        self.assertEqual(mock_entry.call_count, 1)
        self.assertEqual(mock_entry.call_args[0][0], "svc-a")

    def test_prep_disabled_returns_none(self):
        from apps.deployments.tasks.ecosystem.tasks import _prepare_ecosystem_mesh
        self.assertIsNone(_prepare_ecosystem_mesh(self._entries(), mtls_enabled=False))

    def test_prep_fails_on_missing_template(self):
        err, _ = self._run_prep(template_ok=False)
        self.assertIn("template", err)

    def test_prep_fails_on_image(self):
        err, _ = self._run_prep(image_ok=False)
        self.assertIn("image", err)

    def test_prep_fails_on_agent_down(self):
        err, _ = self._run_prep(agent_ok=False)
        self.assertIn("agent", err)
