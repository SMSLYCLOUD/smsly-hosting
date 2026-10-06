"""Addon backend reconciler — closes the ACTIVE-row-vs-missing-backend gap.

An addon row can claim ACTIVE while its backend is gone (removed
container + volume, wiped node, aborted migration): nothing re-checks
after provisioning. This module verifies every ACTIVE (and previously
flagged) addon against its real backend:

- ``shared`` (provision_mode): the logical database must exist on the
  shared server;
- ``container`` on the primary node: the container must exist locally;
- ``container`` on a remote node: the node must know the container
  (existing ``containers/<name>/networks`` endpoint).

Fail-closed throughout: a missing backend flips the row to
``BACKEND_MISSING`` (never deletes, never reprovisions — an empty
fresh backend would mask data loss and split-brain a later restore).
Recovery stays an explicit operator action (reprovision). A backend
that reappears flips the flag back to ACTIVE automatically.
"""
import logging

logger = logging.getLogger(__name__)


def _container_name(addon) -> str:
    from apps.addons.services.addon_provisioner import addon_container_name
    try:
        return addon_container_name(addon)
    except Exception:
        t = str(getattr(addon, 'addon_type', '') or '').lower()
        return f"smsly-addon-{t}-{getattr(addon, 'id', '')}"


def check_addon_backend(addon) -> str | None:
    """Return a missing-reason string, or None when the backend exists.

    Never raises (returns 'check-failed: ...' on infrastructure errors
    so callers can distinguish "gone" from "unknown" — only "gone"
    flips the row).
    """
    try:
        try:
            _rmeta = dict(getattr(addon, 'provider_metadata', None) or {})
        except Exception:
            _rmeta = {}
        if _rmeta.get('mesh_backed'):
            # Mesh-backed row (node copy of a master addon): no local
            # container exists by design — probe the mesh endpoint.
            # Refusal flags BACKEND_MISSING (recovery = reprovision on
            # master + redeploy, never local provisioning).
            try:
                from apps.deployments.tasks.deploy.addons import _probe_mesh_addon
                _probe_mesh_addon(addon)
                return None
            except RuntimeError as exc:
                return f'gone: {exc}'[:160]
            except Exception as exc:
                return f'check-failed: mesh probe: {exc}'[:160]
        mode = str(getattr(addon, 'provision_mode', '') or '').strip() or 'container'
        if mode == 'shared':
            try:
                from urllib.parse import urlparse as _urlparse
                from apps.addons.services.shared_postgres import database_exists
                db = (_urlparse(getattr(addon, 'connection_url', '') or '').path or '').lstrip('/')
                if not db:
                    return 'gone: shared URL has no database'
                return None if database_exists(db) else f'gone: shared database {db!r} missing'
            except Exception as exc:
                return f'check-failed: shared probe: {exc}'[:160]
        name = _container_name(addon)
        try:
            target = str(getattr(addon.service, 'active_target_type', '') or '').lower()
        except Exception:
            target = ''
        try:
            server = getattr(addon.service, 'server', None)
        except Exception:
            server = None
        remote = target in ('remote', 'lite_agent') or (
            server is not None and not getattr(server, 'is_primary', True)
        )

        def _check_node():
            try:
                if server is None:
                    return 'check-failed: remote service has no server'
                from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
                resp = RemoteOrchestrator(server)._request(
                    method='get', path=f'/api/v1/node/containers/{name}/networks/',
                    timeout=30,
                )
                if resp is None:
                    return 'check-failed: node unreachable'
                if resp.status_code == 200:
                    try:
                        body = resp.json() or {}
                    except Exception:
                        body = {}
                    if str(body.get('status', '')) == 'not-found':
                        return f'gone: container {name} not on node {getattr(server, "name", "?")}'
                    return None
                return f'check-failed: node returned {resp.status_code}'
            except Exception as exc:
                return f'check-failed: node probe: {exc}'[:160]

        def _check_local():
            try:
                import docker as _docker
                client = _docker.from_env(timeout=10)
                client.containers.get(name)
                return None
            except Exception:
                return f'gone: local container {name} missing'

        if remote:
            # A remote service's addon backend may live on EITHER host:
            # node-provisioned addons run on the node, master-provisioned
            # (mesh design) run here. Absent in only one home proves
            # nothing — gone requires absence in both; any check failure
            # is unknown, never missing.
            node_verdict = _check_node()
            if node_verdict is None:
                return None
            local_verdict = _check_local()
            if local_verdict is None:
                return None
            if node_verdict.startswith('gone:') and local_verdict.startswith('gone:'):
                return f'{local_verdict} + {node_verdict}'[:160]
            return node_verdict if node_verdict.startswith('check-failed:') else local_verdict
        return _check_local()
    except Exception as exc:
        return f'check-failed: {exc}'[:160]


def reconcile_addon_backends() -> dict:
    """Flag ACTIVE addons whose backend is gone; unflag recovered ones."""
    report: dict = {'missing': [], 'recovered': [], 'unchecked': []}
    try:
        from apps.deployments.models.addons import Addon
    except Exception as exc:
        return {'error': f'models unavailable: {exc}', **report}
    try:
        candidates = list(Addon.objects.filter(
            status__in=(Addon.Status.ACTIVE, Addon.Status.BACKEND_MISSING),
        ).select_related('service'))
    except Exception as exc:
        return {'error': f'query failed: {exc}', **report}
    for addon in candidates:
        name = getattr(addon, 'name', '') or str(getattr(addon, 'id', ''))
        try:
            verdict = check_addon_backend(addon)
        except Exception as exc:
            report['unchecked'].append(name)
            logger.debug('Reconcile: check crashed for %s: %s', name, exc)
            continue
        if verdict is None:
            if addon.status == Addon.Status.BACKEND_MISSING:
                addon.status = Addon.Status.ACTIVE
                addon.save(update_fields=['status', 'updated_at'])
                report['recovered'].append(name)
                logger.warning('Reconcile: backend back for %s — flag cleared', name)
            continue
        if verdict.startswith('gone:'):
            if addon.status != Addon.Status.BACKEND_MISSING:
                addon.status = Addon.Status.BACKEND_MISSING
                addon.save(update_fields=['status', 'updated_at'])
                report['missing'].append(f'{name} ({verdict})')
                logger.error('Reconcile: %s backend %s', name, verdict)
        else:
            report['unchecked'].append(f'{name} ({verdict})')
            logger.debug('Reconcile: %s %s', name, verdict)
    return {'status': 'ok', **report}
