import logging
import os

from apps.deployments.models import (
    PlatformConfig,
)

logger = logging.getLogger(__name__)


class DeploymentMixin:
    def trigger_deploy(self, deployment, remote_service_id, skip_review=False, image_name=None,
                         fast_deploy=False):
        path = f"/api/v1/services/{remote_service_id}/deploy/"
        config = PlatformConfig.load()
        ref = deployment.commit_hash or "HEAD"

        payload = {
            "ref": ref,
            "source_node": config.server_ip or "controller",
            "skip_review": skip_review,
            "fast_deploy": fast_deploy,
        }
        # Ship the master-resolved project bridge: the node has no scope
        # rows, so without this every node deploy lands on flat smsly-net
        # and loses project isolation (internal IP/DNS/egress scoping).
        try:
            from apps.deployments.models.network_scope import ScopedNetwork
            _scope = getattr(deployment, "service", None) and getattr(deployment.service, "project", None)
            if _scope is not None:
                _cfg = ScopedNetwork.resolve_network_config(_scope)
                _name = str(_cfg.get("name", "") or "").strip()
                from apps.deployments.services.network_scope import is_valid_scoped_name
                if _name and is_valid_scoped_name(_name):
                    payload["network"] = {
                        "name": _name,
                        "egress": list(_cfg.get("allowed_egress_networks") or ["0.0.0.0/0"]),
                    }
        except Exception as exc:
            logger.debug("Remote trigger network hint skipped: %s", exc)
        # Ship the vault token: nodes carry no token of their own, so
        # without this vault injection silently never runs on node
        # deploys (the "Token: Missing" state). Same HMAC+TLS channel
        # as env rows; node stores it only when non-blank (never wipes
        # with blank); never logged anywhere on either side.
        try:
            from apps.deployments.services.infisical import resolve_service_token
            _vtok = resolve_service_token()
            if _vtok:
                payload["vault_token"] = _vtok
        except Exception as exc:
            logger.debug("Remote trigger vault token skipped: %s", exc)
        if image_name:
            # Rewrite master-INTERNAL registry refs (registry:5000 /
            # loopback) to the node-routable address. Centralised in
            # registry_routing — resolves PlatformConfig override >
            # WG mesh IP > public IP, and is a no-op when no routable
            # address is configured (single-host installs).
            from apps.deployments.services.registry_routing import image_ref_for_node
            _rewritten = image_ref_for_node(image_name)
            if _rewritten != image_name:
                logger.info(f"Rewrote registry image for remote node: {image_name} -> {_rewritten}")
                image_name = _rewritten

            payload["image_name"] = image_name

        try:
            resp = self._request("POST", path, payload=payload, timeout=60)
            if resp and resp.status_code in (201, 200, 202):
                data = self._parse_json_response(resp, "triggering remote deploy")
                if isinstance(data, dict):
                    remote_id = data.get("deployment_id") or data.get("id")
                    if remote_id:
                        return remote_id
                self._set_last_error(
                    "Remote deploy trigger response did not include a deployment id.",
                    response=resp,
                )
                return None
            if resp is not None:
                self._set_last_error("Failed to trigger remote deploy.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error triggering remote deploy: {e}")
            logger.error(self.last_error)

        return None

    def approve_deployment(self, remote_deployment_id: str, payload: dict | None = None) -> bool:
        path = f"/api/v1/deployments/{remote_deployment_id}/approve/"

        try:
            resp = self._request("POST", path, payload=payload or {}, timeout=15)
            if resp and resp.status_code in (200, 202):
                return True
            if resp is not None:
                self._set_last_error("Failed to approve remote deploy.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error approving remote deploy: {e}")
            logger.error(self.last_error)

        return False

    def poll_deployment(self, remote_deployment_id: str) -> dict:
        path = f"/api/v1/deployments/{remote_deployment_id}/"

        try:
            resp = self._request("GET", path, timeout=10)
            if resp and resp.status_code == 200:
                data = self._parse_json_response(resp, "polling remote deployment")
                return data if isinstance(data, dict) else {}
            if resp is not None:
                self._set_last_error("Failed to poll remote deployment.", response=resp)
        except Exception as exc:
            self._set_last_error(f"Error polling remote deployment: {exc}")

        return {}
