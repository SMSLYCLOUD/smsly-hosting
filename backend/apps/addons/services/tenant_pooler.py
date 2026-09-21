"""Per-tenant pgcat pooler for shared logical Postgres databases.

The platform pgcat fronts the control-plane database. Shared tenant
databases (one logical DB per addon on ``smsly-shared-postgres``) can
optionally sit behind their own pooler (``pgcat-tenants`` container, config
in the ``pgcat_tenants_config`` volume) so app connection storms pool
instead of hitting Postgres directly.

Gated by ``PlatformConfig.tenant_pooling_enabled`` (default ON) with
sticky semantics mirroring ``provision_mode``:
- Existing shared addons keep dialling the server directly (their alias
  already resolves there) — enabling the gate never breaks them.
- New shared provisions attach the POOLER alias and get a rendered pool.
- Disabling the gate only affects future provisions.

The backend pushes the rendered ``pgcat.toml`` on every shared
provision/delete/rotate and restarts the pooler only when the content
changed. The pooler entrypoint waits for the first push.
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import tarfile
import tempfile

logger = logging.getLogger(__name__)

TENANTS_CONTAINER_MARK = 'pgcat-tenants'
TENANTS_TOML_PATH = '/etc/pgcat/pgcat.toml'
TENANT_POOL_SIZE = 8


def _run(args, timeout=60):
    try:
        result = subprocess.run(
            args, capture_output=True, timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or b'failed')
            return {'error': err.decode('utf-8', 'replace').strip()[:500]}
        return {'output': (result.stdout or b'').decode('utf-8', 'replace').strip(),
                'raw': result.stdout or b''}
    except subprocess.TimeoutExpired:
        return {'error': 'command timed out: %s' % ' '.join(args[:3])}
    except Exception as exc:
        return {'error': str(exc)[:500]}


def tenant_pooling_enabled():
    try:
        from apps.deployments.models.platform import PlatformConfig
        return bool(getattr(PlatformConfig.load(), 'tenant_pooling_enabled', True))
    except Exception:
        return False


def tenants_container_name():
    """Resolve the pooler container (compose project prefix varies)."""
    res = _run(['docker', 'ps', '-a', '--format', '{{.Names}}'], timeout=15)
    if res.get('error'):
        return None
    cands = [n for n in res['output'].split() if TENANTS_CONTAINER_MARK in n]
    if not cands:
        return None
    cands.sort(key=len)
    return cands[0]


def container_running(name):
    res = _run(['docker', 'inspect', name, '--format', '{{.State.Running}}'], timeout=15)
    return res.get('output') == 'true'


def list_tenant_pools(with_passwords=False):
    """Metadata (or full creds) for every ACTIVE shared POSTGRES addon."""
    from urllib.parse import urlparse as _urlparse
    from apps.deployments.models.addons import Addon
    pools = []
    rows = Addon.objects.filter(
        addon_type='POSTGRES', status=Addon.Status.ACTIVE,
        provision_mode='shared',
    ).exclude(connection_url='').order_by('name')
    for addon in rows:
        try:
            parsed = _urlparse(addon.connection_url or '')
            alias = parsed.hostname or ''
            user = parsed.username or ''
            db = (parsed.path or '/').lstrip('/') or ''
            password = parsed.password or ''
            if not (alias and user and db and password):
                continue
            pool = {'alias': alias, 'user': user, 'db': db}
            if with_passwords:
                pool['password'] = password
            pools.append(pool)
        except Exception as exc:
            logger.debug("tenant pooler: skipping addon %s: %s", addon.id, exc)
    return pools


def _admin_credentials():
    """Pgcat admin user/pass for the tenants config (same as platform pgcat).

    pgcat refuses to start without them (BadConfig crash-loop, observed
    live). Sourced from the same env the platform pooler renders from —
    backend containers receive the full .env file.
    """
    import os
    user = os.environ.get('PGCAT_ADMIN_USERNAME', 'pgcat_admin') or 'pgcat_admin'
    password = os.environ.get('PGCAT_ADMIN_PASSWORD', '') or ''
    if not password:
        raise RuntimeError(
            'PGCAT_ADMIN_PASSWORD is not set in the backend environment — '
            'refusing to render a tenants config that would crash-loop the pooler.')
    return user, password


def render_tenants_config(pools):
    """Render a pgcat.toml with one transaction pool per tenant alias.

    The [general] block mirrors the keys the pooler binary requires
    (host/port/pool_size/pool_mode/connect_timeout plus the standard
    timeouts) — a host-less general is rejected with
    ``missing field 'host'`` even though the main pooler's own render
    omits them (verified live 2026-09-21: identical binary, /etc-path
    mounted config).

    The trailing [user]/[shards]/[query_router] tables mirror the
    binary's shipped example: without them the parser falls through to
    ``missing field 'user'`` / ``missing field 'shards'``. They define
    only the unused example sharding user (dead 127.0.0.1 backends —
    nothing authenticates as it); tenant traffic uses [pools.*].
    """
    from apps.addons.services.shared_postgres import SHARED_CONTAINER
    admin_user, admin_pass = _admin_credentials()
    lines = [
        '[general]',
        'host = "0.0.0.0"',
        'port = 5432',
        'pool_size = 15',
        'pool_mode = "transaction"',
        'connect_timeout = 5000',
        f'admin_username = "{admin_user}"',
        f'admin_password = "{admin_pass}"',
        'server_lifetime = 86400000',
        'idle_timeout = 60000',
        'dns_cache_enabled = true',
        'dns_cache_ttl = 30000',
        'query_parser_enabled = true',
        'query_parser_read_write_splitting = false',
        'healthcheck_timeout = 5000',
        'healthcheck_delay = 30000',
        'ban_time = 60',
        '',
    ]
    for pool in pools:
        alias = pool['alias']
        lines += [
            f'[pools.{alias}]',
            'pool_mode = "transaction"',
            '',
            f'[pools.{alias}.shards.0]',
            f'servers = [["{SHARED_CONTAINER}", 5432, "primary"]]',
            f'database = "{pool["db"]}"',
            f'[pools.{alias}.users.{pool["user"]}]',
            f'username = "{pool["user"]}"',
            f'pool_size = {TENANT_POOL_SIZE}',
            f'password = "{pool["password"]}"',
            '',
        ]
    # Legacy sharding tables, mirrored from the binary's shipped example.
    # The parser demands top-level [user]/[shards]/[query_router] even
    # when all live traffic uses [pools.*] (verified live 2026-09-21:
    # without them startup fails with missing-field errors). They
    # define only an unused example sharding user against loopback
    # backends — nothing authenticates as it; tenant traffic uses pools.
    lines += [
        '[user]',
        'name = "tenant_sharding_user"',
        'password = "tenant_sharding_user"',
        '',
        '[shards]',
        '',
        '[shards.0]',
        'servers = [["127.0.0.1", 5432, "primary"]]',
        'database = "postgres"',
        '',
        '[query_router]',
        'default_role = "any"',
        '',
    ]
    return '\n'.join(lines) + '\n'


def _read_remote_toml(container):
    res = _run(['docker', 'cp', f'{container}:{TENANTS_TOML_PATH}', '-'], timeout=30)
    if res.get('error') or not res.get('raw'):
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(res['raw'])) as tar:
            member = tar.next()
            if member is None:
                return None
            f = tar.extractfile(member)
            if f is None:
                return None
            return f.read().decode('utf-8')
    except Exception:
        return None


def _write_remote_toml(container, content):
    path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.toml', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        # docker cp preserves mode bits but lands root-owned; the pooler
        # runs as pgcat, so the file must be world-readable (same reason
        # the fallback editor writes 644).
        os.chmod(path, 0o644)
        res = _run(['docker', 'cp', path, f'{container}:{TENANTS_TOML_PATH}'], timeout=60)
        return res
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def push_tenants_config():
    """Render + push tenant pools; restart pooler only when changed.

    Never pushes an unrenderable config: a render failure (e.g. missing
    admin password) returns ok=False and leaves the running pooler
    untouched — pushing a BadConfig would crash-loop it.
    """
    pools = list_tenant_pools(with_passwords=True)
    try:
        content = render_tenants_config(pools)
    except Exception as exc:
        logger.warning("tenant pooler: render failed, pooler untouched: %s", exc)
        return {'ok': False, 'pools': len(pools), 'error': str(exc)[:300]}
    if not content.strip():
        return {'ok': False, 'pools': len(pools),
                'error': 'rendered config is empty — pooler untouched.'}
    container = tenants_container_name()
    if container is None:
        return {'ok': False, 'pools': len(pools),
                'error': 'pgcat-tenants container not found (compose service missing?).'}
    if not container_running(container):
        start = _run(['docker', 'start', container], timeout=60)
        if start.get('error'):
            return {'ok': False, 'pools': len(pools),
                    'error': f'pooler present but could not start: {start["error"]}'}
    current = _read_remote_toml(container)
    if current == content:
        return {'ok': True, 'pools': len(pools), 'changed': False, 'restarted': False}
    res = _write_remote_toml(container, content)
    if res.get('error'):
        return {'ok': False, 'pools': len(pools),
                'error': f'config write failed: {res["error"]}'}
    restart = _run(['docker', 'restart', container], timeout=90)
    if restart.get('error'):
        return {'ok': False, 'pools': len(pools),
                'error': f'config written but pooler restart failed: {restart["error"]}'}
    return {'ok': True, 'pools': len(pools), 'changed': True, 'restarted': True}


def pooler_status():
    """Container state + rendered pool metadata (no passwords) for the UI."""
    from apps.addons.services.shared_postgres import SHARED_CONTAINER  # noqa: F401
    container = tenants_container_name()
    info = {'enabled': tenant_pooling_enabled(), 'container': None, 'pools': []}
    if container is not None:
        res = _run(['docker', 'inspect', container,
                    '--format', '{{.State.Running}}|{{.State.Status}}'], timeout=15)
        if not res.get('error'):
            running, _, stat = (res['output'] + '||').partition('|')
            info['container'] = {
                'name': container,
                'running': running == 'true',
                'status': (stat or '').strip('|') or ('running' if running == 'true' else 'unknown'),
            }
    try:
        info['pools'] = list_tenant_pools(with_passwords=False)
    except Exception as exc:
        info['error'] = str(exc)[:200]
    return info


def attach_pooler_alias(network, alias):
    """Join the pooler to ``network`` with DNS ``alias`` (idempotent)."""
    container = tenants_container_name()
    if container is None:
        raise RuntimeError('pgcat-tenants container not found.')
    from apps.addons.services.shared_postgres import _endpoint_aliases, _run as _sp_run
    current = _endpoint_aliases(container, network)
    if alias in current:
        return
    wanted = list(dict.fromkeys([*current, alias]))
    _sp_run(['docker', 'network', 'disconnect', network, container], timeout=60)
    cmd = ['docker', 'network', 'connect']
    for entry in wanted:
        cmd += ['--alias', entry]
    cmd += [network, container]
    proc = _sp_run(cmd, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f'tenant pooler attach to {network} failed: {(proc.stderr or "").strip()[:200]}')
