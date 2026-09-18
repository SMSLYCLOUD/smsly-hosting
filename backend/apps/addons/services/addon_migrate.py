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
- shared -> container: strip the alias from the shared server, then drop
  the old logical DB.
- container -> shared: remove the old container (which carries the alias).

Callers must restart/redeploy the owning service afterwards: the addon env
vars in the DB are updated, but running containers keep their old env.
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


def sync_addon_env_vars(addon):
    """Refresh the ADDON-source env vars from the current connection URL."""
    from apps.deployments.models import EnvironmentVariable
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


def migrate_addon_mode(addon_id, target_mode):
    """Move a POSTGRES addon between shared pool and dedicated container.

    Raises on failure (after best-effort rollback to the original row).
    Returns a result dict on success.
    """
    from apps.addons.services.addon_provisioner import addon_provisioner
    from apps.addons.services.shared_postgres import SHARED_CONTAINER, drop_logical_db
    from apps.deployments.models.addons import Addon

    if target_mode not in ('shared', 'container'):
        raise ValueError("target_mode must be 'shared' or 'container'.")
    addon = Addon.objects.select_related('service').get(id=addon_id)
    if addon.addon_type != 'POSTGRES':
        raise ValueError("Migration is only supported for POSTGRES addons.")
    if addon.status != Addon.Status.ACTIVE:
        raise ValueError("Addon must be ACTIVE to migrate.")
    server = getattr(addon.service, 'server', None)
    if server is not None and not getattr(server, 'is_primary', False):
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

    logger.info("Migrating addon %s (%s -> %s)", addon.id, old_mode or 'default', target_mode)
    # 1. Dump the source while the row still points at it.
    dump_path = addon_provisioner.create_backup(addon)
    logger.info("Migration dump for addon %s at %s", addon.id, dump_path)

    new_container_created = False
    stripped_nets = []
    try:
        # 2. Flip the row and provision a fresh target (new creds).
        addon.provision_mode = target_mode
        addon.connection_url = ''
        addon.save(update_fields=['provision_mode', 'connection_url', 'updated_at'])
        _cid, new_url = addon_provisioner.provision_dispatch(addon)
        if not new_url:
            raise RuntimeError("Target provisioning returned no connection URL.")
        addon.connection_url = new_url
        addon.coolify_uuid = _cid
        addon.save(update_fields=['connection_url', 'coolify_uuid', 'updated_at'])
        if target_mode == 'container':
            new_container_created = True

        # 3. Restore the dump into the target.
        if not addon_provisioner.restore_backup(addon, dump_path):
            raise RuntimeError("Restore into the migration target failed.")
        # 4. Verify before touching the source.
        if not verify_postgres_url(new_url):
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
            drop_logical_db(str(old_parts.get('username') or ''),
                            str(old_parts.get('database') or ''))
        else:
            ok = addon_provisioner.deprovision_dispatch(container_name, addon, container_name)
            if not ok:
                raise RuntimeError("Target verified but old container removal failed — "
                                   "remove it manually to clear the duplicate network alias.")

        # 6. Point app env at the new credentials.
        sync_addon_env_vars(addon)
        logger.info("Migrated addon %s to %s", addon.id, target_mode)
        return {
            'status': 'ok',
            'target_mode': target_mode,
            'backup_path': dump_path,
            'message': ('Migration complete. Restart/redeploy the owning service '
                        'so it picks up the new connection URL.'),
        }
    except Exception:
        # Roll back to the original row; the source is intact unless the
        # alias was already stripped (step 5) — then re-attach it.
        try:
            addon.provision_mode = old_mode
            addon.connection_url = old_url
            addon.pooler_routed = old_pooled
            addon.save(update_fields=['provision_mode', 'connection_url', 'pooler_routed', 'updated_at'])
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
        raise
