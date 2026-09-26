"""Shared <-> container migration for POSTGRES addons.

Explicit hosting-mode changes after provisioning are rejected by the API
(``AddonSerializer.validate_provision_mode``) because flipping the row would
strand the data. This module is the sanctioned path: dump the source,
provision the target, restore, verify, then drop the source. The source is
never touched until the target is verified, so every failure before cleanup
rolls back to the original row (mode + URL).

Alias handling: the app-facing network alias must resolve to exactly one
backend. Provisioning the target creates a brief duplex (both backends carry
the alias) which is removed before the switch is complete:
- shared -> container: strip the alias from the shared server, sync env,
  re-verify liveness, then drop the old logical DB last (a drop failure
  is a warning, never a rollback — rolling back after the drop would
  point the row at a deleted database).
- container -> shared: remove the old container (which carries the alias).

Write quiesce: the owning service's containers are stopped before the
dump (post-dump writes would otherwise be lost). On success the service
is automatically refreshed onto the new credentials (same image, fresh
env, no rebuild) and stale exact-match URL copies are repointed — no
manual redeploy needed unless the result carries a warning. On failure
the stopped containers are restarted to restore service.

Concurrency: the row is marked MIGRATING inside an atomic check-and-set;
a second concurrent migration sees non-ACTIVE and is rejected.
"""
from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger(__name__)


def _run(args, timeout=60):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return {'error': (result.stderr or result.stdout or 'failed').strip()[:500]}
        return {'output': (result.stdout or '').strip()}
    except subprocess.TimeoutExpired:
        return {'error': 'command timed out: %s' % ' '.join(args[:3])}
    except Exception as exc:
        return {'error': str(exc)[:500]}


def container_network_aliases(container):
    """{network: [aliases]} for a container, or {} when unknown."""
    res = _run(['docker', 'inspect', container,
                '--format', '{{json .NetworkSettings.Networks}}'], timeout=30)
    if res.get('error'):
        return {}
    try:
        nets = json.loads(res['output'] or '{}')
        return {net: list(info.get('Aliases') or [])
                for net, info in (nets or {}).items()}
    except (ValueError, AttributeError):
        return {}


def strip_alias(container, network, alias):
    """Remove one alias from a network endpoint (disconnect + reconnect)."""
    current = container_network_aliases(container).get(network, [])
    remaining = [a for a in current if a != alias]
    res = _run(['docker', 'network', 'disconnect', network, container], timeout=60)
    if res.get('error'):
        return res
    cmd = ['docker', 'network', 'connect']
    for a in remaining:
        cmd += ['--alias', a]
    cmd += [network, container]
    return _run(cmd, timeout=60)


def add_alias(container, network, alias):
    return _run(['docker', 'network', 'connect',
                 '--alias', alias, network, container], timeout=60)


def verify_postgres_url(url, timeout=15):
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=timeout)
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                return cur.fetchone() == (1,)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("migration verify failed: %s", exc)
        return False


def sync_addon_env_vars(addon, old_url=""):
    """Refresh the ADDON-source env vars from the current connection URL.

    Three passes, all gated on ``source == 'ADDON'`` (USER-managed values
    are never touched):
    1. Derived keys from ``parsed_credentials`` (HOST/PORT/USER/...).
    2. Exact-match sweep: any ADDON var still holding the pre-migration
       URL (DATABASE_URL and friends) is repointed at the new backend.
       Without this the app keeps dialling the dropped database with
       dead credentials (2026-09-22 post-migration auth failures).
    3. Canonical key refresh: the provision-time key for the addon type
       (ENV_KEY_MAP, e.g. DATABASE_URL for POSTGRES) is synced when the
       provision created it.
    Per-object save() throughout: ``value`` is encrypted at rest, so a
    queryset ``update()`` would store undecryptable plaintext.
    """
    from apps.deployments.models import EnvironmentVariable
    new_url = str(getattr(addon, 'connection_url', '') or '').strip()
    if not new_url:
        return
    try:
        creds = addon.parsed_credentials or {}
    except Exception:
        creds = {}
    for key, value in creds.items():
        EnvironmentVariable.objects.update_or_create(
            service=addon.service,
            key=key,
            defaults={
                'value': value,
                'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL'),
                'source': 'ADDON',
            },
        )
    if old_url:
        # NOTE: EncryptedCharField (Fernet, random IV) never matches a
        # plaintext ORM filter — an exact `value=old_url` lookup encrypts
        # with a fresh IV and matches nothing. Compare decrypted values
        # in Python (per-object save keeps the ciphertext valid).
        for var in EnvironmentVariable.objects.filter(
            service=addon.service, source='ADDON',
        ):
            if var.value != old_url:
                continue
            var.value = new_url
            var.save(update_fields=['value', 'updated_at'])
            logger.info("Migration env sync: repointed %s at new backend",
                        var.key)
    try:
        from apps.addons.services.addon_provisioner import AddonProvisioner
        env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
    except Exception:
        env_key = None
    if env_key:
        for var in EnvironmentVariable.objects.filter(
            service=addon.service, key=env_key, source='ADDON',
        ):
            if var.value != new_url:
                var.value = new_url
                var.save(update_fields=['value', 'updated_at'])


def _service_container_names(service) -> list[str]:
    """Running container names owned by the service (label, then name)."""
    names: list[str] = []
    res = _run(['docker', 'ps', '--format', '{{.Names}}',
                '--filter', f'label=smsly.service_id={getattr(service, "id", "")}'],
               timeout=30)
    if not res.get('error'):
        names = [n for n in (res.get('output') or '').split() if n]
    if not names:
        svc_name = str(getattr(service, 'name', '') or '').strip()
        if svc_name:
            res = _run(['docker', 'ps', '--format', '{{.Names}}',
                        '--filter', f'name=^{svc_name}$'], timeout=30)
            if not res.get('error'):
                names = [n for n in (res.get('output') or '').split() if n]
    return names


def _stop_service_containers(names: list[str]) -> None:
    """Stop service containers pre-dump (write quiesce). Raises on failure —
    nothing has been touched yet at that point, so aborting is safe."""
    for name in names:
        res = _run(['docker', 'stop', '--timeout', '30', name], timeout=60)
        if res.get('error'):
            raise RuntimeError(f"Could not stop service container {name}: {res['error']}")


def _start_service_containers(names: list[str]) -> None:
    """Best-effort restart (rollback path only — never raises)."""
    for name in names:
        res = _run(['docker', 'start', name], timeout=60)
        if res.get('error'):
            logger.warning("Migration rollback: could not restart %s: %s", name, res['error'])


def _verify_target_via_exec(container_name: str, url: str, timeout=30) -> bool:
    """SELECT 1 via `docker exec psql` inside the backing container.

    The celery worker is not on project-scoped bridges, so addon DNS
    names never resolve from here — direct psycopg2 dials fail even
    when the target is healthy. Exec sidesteps Docker DNS entirely.
    """
    try:
        from urllib.parse import urlparse as _urlparse
        parsed = _urlparse(url or '')
        user = parsed.username or 'postgres'
        db = (parsed.path or '').lstrip('/') or 'postgres'
        cmd = ['docker', 'exec', container_name,
               'psql', '-U', user, '-d', 'postgres', '-tAc', 'SELECT 1']
        env = dict(__import__('os').environ)
        if parsed.password:
            env['PGPASSWORD'] = parsed.password
        import subprocess as _sp
        result = _sp.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return result.returncode == 0 and '1' in (result.stdout or '')
    except Exception as exc:
        logger.debug("Exec verify failed for %s: %s", container_name, exc)
        return False


def _claim_for_migration(addon_id):
    """Atomically check ACTIVE and mark MIGRATING (concurrency guard).

    A second concurrent migration blocks here, then sees MIGRATING
    instead of ACTIVE and is rejected — no interleaved dumps.
    """
    from django.db import transaction

    from apps.deployments.models.addons import Addon
    with transaction.atomic():
        addon = Addon.objects.select_for_update().select_related('service').get(id=addon_id)
        if addon.status != Addon.Status.ACTIVE:
            raise ValueError(
                "Addon must be ACTIVE to migrate "
                f"(current: {addon.status} — another migration may be running)."
            )
        addon.status = Addon.Status.MIGRATING
        addon.save(update_fields=['status', 'updated_at'])
        return addon


def _release_migration_claim(addon, status=None) -> None:
    """Best-effort status restore (never raises — rollback path)."""
    try:
        from apps.deployments.models.addons import Addon
        addon.status = status or Addon.Status.ACTIVE
        addon.save(update_fields=['status', 'updated_at'])
    except Exception as exc:
        logger.warning("Could not restore addon %s status: %s",
                       getattr(addon, 'id', '?'), exc)


def migrate_addon_mode(addon_id, target_mode, stop_services=True):
    """Move a POSTGRES addon between shared pool and dedicated container.

    Raises on failure (after best-effort rollback to the original row).
    Returns a result dict on success.

    stop_services (default True) stops the owning service's containers
    before the dump so no writes land after it — without quiesce,
    post-dump rows are silently lost. On success the service is
    automatically refreshed onto the new URL (same image, no rebuild).
    On failure the stopped containers are restarted to restore service.
    """
    from apps.addons.services.addon_provisioner import addon_provisioner
    from apps.addons.services.shared_postgres import SHARED_CONTAINER, drop_logical_db
    from apps.deployments.models.addons import Addon

    if target_mode not in ('shared', 'container'):
        raise ValueError("target_mode must be 'shared' or 'container'.")
    addon = _claim_for_migration(addon_id)
    if addon.addon_type != 'POSTGRES':
        _release_migration_claim(addon)
        raise ValueError("Migration is only supported for POSTGRES addons.")
    server = getattr(addon.service, 'server', None)
    if server is not None and not getattr(server, 'is_primary', False):
        _release_migration_claim(addon)
        raise ValueError("Migration is only supported for local (primary-node) addons.")
    current_shared = str(getattr(addon, 'provision_mode', '') or '') == 'shared'
    if (target_mode == 'shared') == current_shared:
        raise ValueError(f"Addon is already on '{target_mode}'.")

    old_mode = str(getattr(addon, 'provision_mode', '') or '')
    old_url = str(getattr(addon, 'connection_url', '') or '').strip()
    old_pooled = bool(getattr(addon, 'pooler_routed', False))
    if not old_url:
        raise ValueError("Addon has no connection URL — reprovision it first.")
    old_parts = addon_provisioner._parse_connection_url(old_url)
    alias = str(old_parts.get('hostname') or '').strip()
    if not alias:
        raise ValueError("Could not determine the addon network alias from its URL.")
    container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"

    # Credential preservation: the target backend is created with the
    # SOURCE's username/password (and alias), so every existing copy of
    # the credentials keeps working and dump/restore role ownership
    # lines resolve. The database name is preserved too when it is free
    # on the shared server; only a taken name forces a deterministic
    # temp staging name (retry-convergent — leftovers are dropped
    # before restore). Container targets get a fresh cluster, so the
    # original db name is reused verbatim and the final URL is
    # byte-identical to the old one.
    from urllib.parse import quote as _quote
    old_user = str(old_parts.get('username') or '')
    old_password = str(old_parts.get('password') or '')
    old_db = str(old_parts.get('database') or '')
    old_port = str(old_parts.get('port') or '5432')
    if not old_user or not old_password or not old_db:
        raise ValueError(
            "Addon connection URL is missing auth details (user/password/"
            "database) — reprovision it first; migration cannot preserve "
            "credentials it cannot read.")
    staging_db = old_db
    if target_mode == 'shared':
        temp_db = f"{old_db[:40]}__mig_{str(addon.id).replace('-', '')[:8]}"
        try:
            from apps.addons.services.shared_postgres import (
                database_exists as _shared_db_exists,
            )
            if _shared_db_exists(old_db):
                staging_db = temp_db
                logger.info(
                    "Migration %s: database name %r taken on shared — "
                    "staging under %r", addon.id, old_db, staging_db)
            else:
                logger.info(
                    "Migration %s: database name %r free on shared — "
                    "preserving it", addon.id, old_db)
        except Exception as exc:
            # Shared server unreachable — staging safe; the nested
            # provision will surface the real error.
            staging_db = temp_db
            logger.debug("Migration %s: shared name probe failed (%s) — "
                         "staging under %r", addon.id, exc, staging_db)
    staging_url = (
        f"postgresql://{_quote(old_user, safe='')}:{_quote(old_password, safe='')}"
        f"@{alias}:{old_port}/{staging_db}"
    )

    logger.info("Migrating addon %s (%s -> %s)", addon.id, old_mode or 'default', target_mode)

    # 0. Write quiesce: stop the owning service's containers BEFORE the
    #    dump, otherwise post-dump writes are silently lost. Stopped
    #    containers stay stopped — the stored URL changes, so only a
    #    REDEPLOY (not a restart) picks it up. Restored on failure.
    stopped_containers: list[str] = []
    if stop_services:
        stopped_containers = _service_container_names(addon.service)
        if stopped_containers:
            logger.info("Migration quiesce: stopping %s", stopped_containers)
            _stop_service_containers(stopped_containers)

    # 1. Dump the source while the row still points at it.
    dump_path = addon_provisioner.create_backup(addon)
    logger.info("Migration dump for addon %s at %s", addon.id, dump_path)

    new_container_created = False
    new_url_set = False
    stripped_nets = []
    source_cleanup_warning = ''
    try:
        # 2. Flip the row and provision the target. The row keeps
        # credential-bearing URL shape (same user/password/alias — only
        # the db name differs on shared targets), so both provision
        # paths converge on the ORIGINAL auth details instead of fresh
        # random ones: container recreates reuse the persisted URL, and
        # shared logical-DB creation reuses user/db/password from it.
        addon.provision_mode = target_mode
        addon.connection_url = staging_url
        addon.save(update_fields=['provision_mode', 'connection_url', 'updated_at'])
        _cid, new_url = addon_provisioner.provision_dispatch(addon)
        if not new_url:
            raise RuntimeError("Target provisioning returned no connection URL.")
        addon.connection_url = new_url
        addon.coolify_uuid = _cid
        addon.save(update_fields=['connection_url', 'coolify_uuid', 'updated_at'])
        new_url_set = True
        if target_mode == 'container':
            new_container_created = True

        # 3. Restore the dump into the target. On shared targets the
        # staging DB may hold a previous attempt's partial data (same
        # deterministic name) — drop it first so restore starts clean.
        # Role is untouched (shared with the live source).
        if target_mode == 'shared':
            try:
                from apps.addons.services.shared_postgres import (
                    drop_database_only,
                )
                drop_database_only(staging_db)
            except Exception as exc:
                logger.debug("Migration staging cleanup skipped: %s", exc)
        if not addon_provisioner.restore_backup(addon, dump_path):
            raise RuntimeError("Restore into the migration target failed.")
        # 4. Verify before touching the source: direct dial first, then
        #    container-exec (the worker is not on project-scoped
        #    bridges, so addon DNS names never resolve from here).
        if not verify_postgres_url(new_url):
            exec_container = (
                container_name if target_mode == 'container'
                else SHARED_CONTAINER
            )
            if not _verify_target_via_exec(exec_container, new_url):
                raise RuntimeError("Target database failed verification (SELECT 1).")

        # 5. Remove the old backend + its alias.
        if target_mode == 'container':
            for net, aliases in container_network_aliases(SHARED_CONTAINER).items():
                if alias in aliases:
                    res = strip_alias(SHARED_CONTAINER, net, alias)
                    if res.get('error'):
                        raise RuntimeError(
                            f"Could not move alias off shared server ({net}): {res['error']}")
                    stripped_nets.append(net)
        else:
            ok = addon_provisioner.deprovision_dispatch(container_name, addon, container_name)
            if not ok:
                raise RuntimeError("Target verified but old container removal failed — "
                                   "remove it manually to clear the duplicate network alias.")

        # 6. Point app env at the new credentials.
        sync_addon_env_vars(addon, old_url=old_url)
        # 7. Final liveness re-check on the new backend, then drop the
        #    source LAST. A drop failure here is a warning (orphaned
        #    source), never a rollback — rolling back after the drop
        #    would point the row at a deleted database.
        if not verify_postgres_url(new_url):
            exec_container = (
                container_name if target_mode == 'container'
                else SHARED_CONTAINER
            )
            if not _verify_target_via_exec(exec_container, new_url):
                raise RuntimeError("Target lost liveness after env sync — aborting before source drop.")
        if target_mode == 'container':
            # A dedicated container dials direct — clear any pooled flag
            # left over from the shared era (pooler-aware readers would
            # otherwise misroute it). Shared targets get the flag
            # re-evaluated by the nested provision itself.
            addon.pooler_routed = False
            addon.save(update_fields=['pooler_routed', 'updated_at'])
            try:
                drop_logical_db(str(old_parts.get('username') or ''),
                                str(old_parts.get('database') or ''))
            except Exception as exc:
                source_cleanup_warning = f"Old logical DB not dropped: {exc}"
                logger.warning("Migration source cleanup failed for %s: %s", addon.id, exc)
        addon.status = Addon.Status.ACTIVE
        addon.save(update_fields=['status', 'updated_at'])
        logger.info("Migrated addon %s to %s", addon.id, target_mode)
        result_push_warning = ''
        if target_mode == 'shared':
            # The nested provision's pooler push ran while this row was
            # MIGRATING, and the tenant render only includes ACTIVE
            # shared rows — so the pooler never learned this user.
            # Push now that we're ACTIVE; without it every app
            # connection fails with "no such user" / SASL
            # authentication failed (2026-09-26 incident).
            # Best-effort: the data is safe on shared regardless, but
            # a failed push needs an immediate manual
            # shared-pooler-push, so log loudly (never debug).
            try:
                from apps.addons.services.tenant_pooler import (
                    push_tenants_config as _push_tenants,
                )
                _push_result = _push_tenants() or {}
                if not _push_result.get('ok'):
                    logger.error(
                        "Migration pooler push FAILED for %s: %s — "
                        "run shared-pooler-push before redeploying the service",
                        addon.id, _push_result.get('error'))
                    result_push_warning = (
                        "Tenant pooler push failed "
                        f"({_push_result.get('error')}); run shared-pooler-push, "
                        "then redeploy the service.")
                else:
                    result_push_warning = ''
                    logger.info(
                        "Migration pooler push for %s: pools=%s changed=%s",
                        addon.id, _push_result.get('pools'),
                        _push_result.get('changed'))
            except Exception as exc:
                logger.error(
                    "Migration pooler push skipped for %s: %s — "
                    "run shared-pooler-push before redeploying the service",
                    addon.id, exc)
                result_push_warning = (
                    f"Tenant pooler push skipped ({exc}); run "
                    "shared-pooler-push, then redeploy the service.")
        result = {
            'status': 'ok',
            'target_mode': target_mode,
            'backup_path': dump_path,
            'message': ('Migration complete. REDEPLOY the owning service '
                        'so it picks up the new connection URL (a plain '
                        'restart keeps the old env).'),
        }
        if source_cleanup_warning:
            result['source_cleanup_warning'] = source_cleanup_warning
        if target_mode == 'shared' and result_push_warning:
            result['pooler_push_warning'] = result_push_warning
        # 8. Hands-free finish: repoint stale URL copies and roll the
        #    service onto the new credentials WITHOUT a rebuild.
        #    Best-effort throughout — the data is safe either way, so
        #    failures surface as notes, never rollbacks.
        auto_notes = []
        final_url = str(getattr(addon, 'connection_url', '') or '').strip()
        if old_url and final_url and old_url != final_url:
            # USER-managed vars holding the pre-migration URL verbatim
            # are stale by definition (same string = same dead
            # backend) — repoint them. Anything user-customized
            # (different string) is left untouched. Python-side
            # comparison: EncryptedCharField never matches a plaintext
            # ORM filter (Fernet random IV).
            try:
                from apps.deployments.models import (
                    EnvironmentVariable as _Env,
                )
                for var in _Env.objects.filter(
                    service=addon.service,
                ).exclude(source='ADDON'):
                    if var.value != old_url:
                        continue
                    var.value = final_url
                    var.save(update_fields=['value', 'updated_at'])
                    auto_notes.append(f"repointed {var.key}")
                    logger.info("Migration auto-finish: repointed %s",
                                var.key)
            except Exception as exc:
                auto_notes.append(f"env repoint skipped: {exc}")
        if stopped_containers:
            # Quiesce left the app stopped — start it, then recreate
            # with fresh env (same image, new credentials, no build).
            _start_service_containers(stopped_containers)
            try:
                from apps.deployments.services.container_refresh import (
                    recreate_with_fresh_env as _recreate,
                )
                _refresh = _recreate(addon.service) or {}
                auto_notes.append(
                    f"container refreshed ({_refresh.get('container_id', '?')})")
                logger.info("Migration auto-finish: refreshed %s",
                            addon.service.name)
            except Exception as exc:
                auto_notes.append(
                    "container refresh skipped — redeploy the service "
                    f"manually so it picks up the new URL ({exc})")
                logger.warning("Migration auto-finish refresh failed "
                               "for %s: %s", addon.service.name, exc)
        if auto_notes:
            result['auto_finish'] = '; '.join(auto_notes)
            if any('container refreshed' in n for n in auto_notes):
                result['message'] = (
                    'Migration complete. The owning service was '
                    'automatically refreshed onto the new connection URL '
                    '(same image, no rebuild).')
        return result
    except Exception:
        # Roll back to the original row; the source is intact unless the
        # alias was already stripped (step 5) — then re-attach it.
        # Partially-created shared backends are dropped (fresh creds —
        # nothing else references them).
        if new_url_set and target_mode == 'shared':
            try:
                from urllib.parse import urlparse as _urlparse
                _parsed = _urlparse(addon.connection_url or '')
                if _parsed.username and (_parsed.path or '').lstrip('/'):
                    drop_logical_db(_parsed.username, (_parsed.path or '').lstrip('/'))
            except Exception as exc:
                logger.debug("Migration orphan cleanup skipped: %s", exc)
        try:
            addon.provision_mode = old_mode
            addon.connection_url = old_url
            addon.pooler_routed = old_pooled
            addon.status = Addon.Status.ACTIVE
            addon.save(update_fields=['provision_mode', 'connection_url',
                                      'pooler_routed', 'status', 'updated_at'])
        except Exception as save_exc:
            logger.error("Migration rollback row-restore failed for %s: %s", addon.id, save_exc)
        for net in stripped_nets:
            res = add_alias(SHARED_CONTAINER, net, alias)
            if res.get('error'):
                logger.error("Migration rollback alias re-attach failed (%s): %s", net, res['error'])
        if target_mode == 'shared':
            # The shared server may carry the alias from the aborted
            # provision while the original container still serves it —
            # strip it back so DNS resolves to exactly one backend.
            for net, aliases in container_network_aliases(SHARED_CONTAINER).items():
                if alias in aliases:
                    res = strip_alias(SHARED_CONTAINER, net, alias)
                    if res.get('error'):
                        logger.error("Migration rollback alias strip failed (%s): %s", net, res['error'])
        if new_container_created:
            _run(['docker', 'rm', '-f', container_name], timeout=90)
        if target_mode == 'shared' and staging_db != old_db:
            # Remove our own staging database so retries start clean.
            # Role untouched — still owns the live source.
            try:
                from apps.addons.services.shared_postgres import (
                    drop_database_only,
                )
                drop_database_only(staging_db)
            except Exception as exc:
                logger.debug("Migration staging rollback cleanup skipped: %s", exc)
        if stopped_containers:
            _start_service_containers(stopped_containers)
        raise
