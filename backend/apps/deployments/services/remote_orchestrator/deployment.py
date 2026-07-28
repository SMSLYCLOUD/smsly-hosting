import logging
import os

from apps.deployments.models import (
    PlatformConfig,
)

logger = logging.getLogger(__name__)


class DeploymentMixin:
    def trigger_deploy(self, deployment, remote_service_id, skip_review=False, image_name=None):
        path = f"/api/v1/services/{remote_service_id}/deploy/"
        config = PlatformConfig.load()
        ref = deployment.commit_hash or "HEAD"

        payload = {
            "ref": ref,
            "source_node": config.server_ip or "controller",
            "skip_review": skip_review,
        }
        if image_name:
            internal_registry_markers = ["registry:5000", "localhost:5000", "127.0.0.1:5000"]
            if any(marker in image_name for marker in internal_registry_markers):
                from apps.deployments.services.provisioner import _get_master_mesh_ip
                master_ip = _get_master_mesh_ip() or os.environ.get("PUBLIC_IP") or "127.0.0.1"

                for marker in internal_registry_markers:
                    image_name = image_name.replace(marker, f"{master_ip}:5000")

                logger.info(f"Rewrote registry image for remote node: {image_name} (via {master_ip})")

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
