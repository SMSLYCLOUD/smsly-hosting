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
        # Ship the SOPS platform keypair: nodes auto-create their own age
        # keypair on first use, which can never decrypt master-exported
        # bundles (verify/decrypt unprovisioned on nodes). Same HMAC+TLS
        # channel and never-log handling as the vault token. Fail-open.
        try:
            from apps.deployments.services.secrets_sops import ensure_age_keypair
            _pub, _priv = ensure_age_keypair()
            if _pub.startswith("age1") and _priv.startswith("AGE-SECRET-KEY-"):
                payload["sops_age_public"] = _pub
                payload["sops_age_private"] = _priv
        except Exception as exc:
            logger.debug("Remote trigger SOPS key skipped: %s", exc)
        # Ship this service's SOPS bundle file (if exported): nodes
        # verify/decrypt against local rows + the converged keypair.
        # Encrypted at rest in transit terms (SOPS envelope); still
        # never logged. Fail-open.
        try:
            from apps.deployments.services.secrets_sops import read_service_bundle
            _svc_b = getattr(deployment, "service", None)
            if _svc_b is not None:
                _bundle = read_service_bundle(_svc_b)
                if _bundle:
                    payload["sops_bundle"] = _bundle
        except Exception as exc:
            logger.debug("Remote trigger SOPS bundle skipped: %s", exc)
        # Ship addon rows for node consumption (mesh-backed): the node
        # pipeline, readiness gates, and shortcode resolution all read
        # node-local Addon rows, which are otherwise always empty after
        # sync. Only ACTIVE addons with mesh-routable URLs; the node
        # applies them as mesh-backed rows (never provisioned locally).
        # Fail-open: never blocks deploy on addon/mesh errors.
        try:
            from apps.deployments.services.addon_mesh import mesh_url_for_addon
            _svc2 = getattr(deployment, "service", None)
            _addon_rows = []
            if _svc2 is not None:
                for _addon in _svc2.addons.exclude(status="DELETED"):
                    if getattr(_addon, "status", "") != "ACTIVE":
                        continue
                    _mesh_url = mesh_url_for_addon(_addon)
                    if not _mesh_url:
                        continue
                    _meta = dict(getattr(_addon, "provider_metadata", None) or {})
                    _addon_rows.append({
                        "name": str(getattr(_addon, "name", "") or "")[:255],
                        "addon_type": str(getattr(_addon, "addon_type", "") or "")[:20],
                        "connection_url": _mesh_url[:512],
                        "mesh_forward_port": _meta.get("mesh_forward_port"),
                    })
                    if len(_addon_rows) >= 50:
                        break
            if _addon_rows:
                payload["addons"] = _addon_rows
        except Exception as exc:
            logger.debug("Remote trigger addon sync skipped: %s", exc)
        # Ship volume definitions: node deploys read node-local Volume
        # rows for mounts, which are otherwise always empty after sync
        # (apps write to container FS instead of their volume). Data does
        # NOT transfer — fresh empty docker volumes are created on the
        # node; use transfer/backup for data seeding. Validated names,
        # mount paths, and size bounds; fail-open.
        try:
            _svc3 = getattr(deployment, "service", None)
            _vol_rows = []
            if _svc3 is not None:
                for _vol in _svc3.volumes.all()[:20]:
                    _vname = str(getattr(_vol, "name", "") or "").strip()[:255]
                    _vpath = str(getattr(_vol, "mount_path", "") or "").strip()[:255]
                    try:
                        _vsize = int(getattr(_vol, "size_gb", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if not _vname or not _vpath:
                        continue
                    if not 1 <= _vsize <= 1000:
                        continue
                    _vol_rows.append({
                        "name": _vname, "mount_path": _vpath, "size_gb": _vsize,
                    })
            if _vol_rows:
                payload["volumes"] = _vol_rows
        except Exception as exc:
            logger.debug("Remote trigger volume sync skipped: %s", exc)
        # Ship mesh-rewritten addon env overrides as well (belt over the
        # row sync above): env rows apply even if addon-row sync is ever
        # skipped. Fail-open.
        try:
            from apps.deployments.services.addon_mesh import rewrite_env_for_mesh
            _svc = getattr(deployment, "service", None)
            if _svc is not None:
                _mesh = rewrite_env_for_mesh(_svc)
                if _mesh:
                    payload["mesh_env"] = dict(list(_mesh.items())[:200])
        except Exception as exc:
            logger.debug("Remote trigger mesh env skipped: %s", exc)
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
