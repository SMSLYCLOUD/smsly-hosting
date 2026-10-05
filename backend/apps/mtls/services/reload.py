"""Manual mTLS reload for mTLS-enabled projects/services.

A reload pulls the latest GitHub state (normal manual redeploy from
HEAD) AND refreshes every mTLS component for the service: the Envoy
sidecar on the running container (reattach/remount when stale) and
the SPIRE registration entries (global sync task, idempotent).

Scope rules (fail-closed):
- Services without an enabled MtlsConfig are rejected (400), never
  silently redeployed without mTLS.
- Sidecar refresh runs only where the service executes locally; for
  remote (node) services the node pipeline owns sidecar injection,
  so the reload reports it as node-handled instead of touching the
  wrong Docker daemon.
- Per-component results are reported independently — a sidecar hiccup
  never masks a successful deploy trigger and vice versa.
"""
import logging

logger = logging.getLogger(__name__)


def _mtls_config_for(service):
    try:
        from apps.mtls.models import MtlsConfig
        return MtlsConfig.objects.filter(service=service, enabled=True).first()
    except Exception:
        return None


def _is_remote_service(service) -> bool:
    try:
        from apps.deployments.utils.target import resolve_active_execution_target
        target = resolve_active_execution_target(service)
        return target.get("target_type") in ("remote", "lite_agent")
    except Exception:
        return False


def reload_service_components(service) -> dict:
    """Refresh live mTLS components (no rebuild). Returns status dict."""
    result: dict = {"sidecar": None, "spiffe": None}
    if _is_remote_service(service):
        result["sidecar"] = {
            "status": "node-handled",
            "detail": "Remote service: sidecar injection is owned by the node deploy pipeline.",
        }
    else:
        try:
            from apps.mtls.services.envoy_sidecar import EnvoySidecar
            result["sidecar"] = EnvoySidecar.reattach_if_stale(service)
        except Exception as exc:
            logger.warning("mTLS reload sidecar refresh failed for %s: %s", service.name, exc)
            result["sidecar"] = {"status": "error", "detail": f"{exc.__class__.__name__}"}
    try:
        from apps.deployments.tasks_spiffe import sync_spiffe_entries_task
        sync_spiffe_entries_task.delay()
        result["spiffe"] = {"status": "sync-queued"}
    except Exception as exc:
        logger.warning("mTLS reload spiffe sync enqueue failed: %s", exc)
        result["spiffe"] = {"status": "error", "detail": f"{exc.__class__.__name__}"}
    return result


def reload_service_mtls(service, deploy_response) -> dict:
    """Combine a deploy trigger response with a component refresh.

    ``deploy_response`` is the DRF Response from the manual-deploy
    trigger (any status). Components refresh regardless so a reload is
    never a silent no-op when a build is already queued (409).
    """
    components = reload_service_components(service)
    try:
        status_code = int(getattr(deploy_response, "status_code", 0) or 0)
    except Exception:
        status_code = 0
    data = getattr(deploy_response, "data", None)
    deployment_id = None
    try:
        if isinstance(data, dict):
            deployment_id = str(data.get("id") or "")
    except Exception:
        pass
    return {
        "service": getattr(service, "name", ""),
        "deploy_triggered": 200 <= status_code < 300,
        "deploy_status": status_code,
        "deployment_id": deployment_id,
        "components": components,
    }
