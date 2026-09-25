"""Per-tenant pooler for shared logical Postgres databases.

Engine: PgBouncer (migrated 2026-09-25 from a pinned pgcat fork that
could not complete SASL — ``Unsupported authentication mechanism: 10``
on every new server connection against SCRAM-only servers, with
ban/unban churn on top).

Container/service/volume names intentionally still say ``pgcat``:
renaming would touch install.sh, monitors, aliases, and docs for zero
functional gain. Only the engine + config format changed.

The platform pgcat fronts the control-plane database. Shared tenant
databases (one logical DB per addon on ``smsly-shared-postgres``) sit
behind their own pooler (``pgcat-tenants`` container serving
``pgbouncer.ini``, config in the ``pgcat_tenants_config`` volume) so
app connection storms pool instead of hitting Postgres directly.

Gated by ``PlatformConfig.tenant_pooling_enabled`` (default ON) with
sticky semantics mirroring ``provision_mode``:
- Existing shared addons keep dialling the server directly (their alias
  already resolves there) — enabling the gate never breaks them.
- New shared provisions attach the POOLER alias and get a rendered pool.
- Disabling the gate only affects future provisions.

The backend pushes the rendered ``pgbouncer.ini`` + ``userlist.txt``
on every shared provision/delete/rotate and restarts the pooler only
when the content changed. The pooler entrypoint waits for the first
push.
"""
from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tarfile
import tempfile

logger = logging.getLogger(__name__)

TENANTS_CONTAINER_MARK = 'pgcat-tenants'
# Stale pgcat artifact (left in the volume, ignored by pgbouncer).
TENANTS_TOML_PATH = '/etc/pgcat/pgcat.toml'
TENANTS_INI_PATH = '/etc/pgcat/pgbouncer.ini'
TENANTS_USERLIST_PATH = '/etc/pgcat/userlist.txt'
TENANT_POOL_SIZE = 8

# INI-safe tokens: alias is an INI key, user/db travel inside
# `key = host=.. dbname=..` values, passwords inside double quotes.
_SAFE_TOKEN = re.compile(r'^[A-Za-z0-9_.\-]+$')


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


def _check_pool_token(kind, value):
    if not value or not _SAFE_TOKEN.match(value):
        raise RuntimeError(
            f"tenants render: unsafe {kind} {value!r} "
            f"(allowed: {_SAFE_TOKEN.pattern})")
    return value


def _check_password(user, password):
    if not password or '"' in password or '\\' in password or any(
            ord(c) < 32 for c in password):
        raise RuntimeError(
            f"tenants render: unusable password for user {user!r} "
            "(empty, quote, backslash, or control chars — "
            "userlist.txt cannot quote them; rotate the credential)")
    return password


def render_tenants_config(pools):
    """Render (pgbouncer.ini, userlist.txt): one transaction pool per alias.

    Client user == server role with the same password (exactly the old
    pgcat model): PgBouncer authenticates the client against userlist
    and dials the server as that role. ``auth_type =
    scram-sha-256`` with plaintext userlist secrets is what the pgcat
    fork could not do.
    """
    from apps.addons.services.shared_postgres import SHARED_CONTAINER
    ini = [
        '; Rendered by platform push_tenants_config — do not edit.',
        '[databases]',
    ]
    users = []
    seen_db_keys = set()
    for pool in pools:
        alias = _check_pool_token('alias', pool['alias'])
        user = _check_pool_token('user', pool['user'])
        db = _check_pool_token('database', pool['db'])
        password = _check_password(user, pool.get('password') or '')
        # PgBouncer routes by DATABASE name, but provisioned URLs carry
        # the pool ALIAS as host with the real dbname as database
        # (postgresql://user:pw@ALIAS/db). Register both keys so the
        # URL works unchanged from the pgcat era.
        keys = [alias] if alias == db else [alias, db]
        for key in keys:
            if key in seen_db_keys:
                raise RuntimeError(
                    f"tenants render: duplicate pool key {key!r} "
                    "(two pools resolve to the same database name)")
            seen_db_keys.add(key)
            ini.append(
                f'{key} = host={SHARED_CONTAINER} port=5432 dbname={db}')
        users.append((user, password))
    ini += [
        '',
        '[pgbouncer]',
        'listen_addr = 0.0.0.0',
        'listen_port = 5432',
        'auth_type = scram-sha-256',
        f'auth_file = {TENANTS_USERLIST_PATH}',
        'pool_mode = transaction',
        'max_client_conn = 200',
        f'default_pool_size = {TENANT_POOL_SIZE}',
        'min_pool_size = 0',
        'reserve_pool_size = 2',
        'reserve_pool_timeout = 3',
        'server_reset_query = DISCARD ALL',
        'server_check_query = select 1',
        'server_check_delay = 30',
        'server_lifetime = 3600',
        'server_idle_timeout = 600',
        'server_connect_timeout = 15',
        'query_timeout = 0',
        'ignore_startup_parameters = extra',
        '',
    ]
    ul = [f'"{user}" "{password}"' for user, password in users]
    ini_content = '\n'.join(ini)
    ul_content = '\n'.join(ul) + ('\n' if ul else '')
    validate_rendered_config(ini_content, ul_content)
    return ini_content, ul_content


def validate_rendered_config(ini_content, userlist_content=""):
    """Fail closed when the render would crash-loop the pooler.

    Raises RuntimeError listing what's missing. Called by
    render_tenants_config so a bad render never reaches the volume
    (push_tenants_config treats it as a failed render and leaves the
    running pooler untouched).
    """
    for section in ('[databases]', '[pgbouncer]'):
        if section not in ini_content:
            raise RuntimeError(
                f"tenants render missing {section} section")
    for key in ('listen_port = 5432', 'auth_type = scram-sha-256',
                f'auth_file = {TENANTS_USERLIST_PATH}',
                'pool_mode = transaction'):
        if key not in ini_content:
            raise RuntimeError(f"tenants render missing {key!r}")
    seen_users = set()
    for lineno, line in enumerate(
            (userlist_content or '').splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        m = re.fullmatch(r'"([^"]+)" "([^"]+)"', line)
        if not m:
            raise RuntimeError(
                f"tenants userlist line {lineno} malformed")
        user, password = m.group(1), m.group(2)
        if not _SAFE_TOKEN.match(user) or not password:
            raise RuntimeError(
                f"tenants userlist line {lineno} has unsafe user/empty password")
        if user in seen_users:
            raise RuntimeError(
                f"tenants userlist duplicates user {user!r}")
        seen_users.add(user)


def _read_remote_file(container, remote_path):
    res = _run(['docker', 'cp', f'{container}:{remote_path}', '-'], timeout=30)
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


def _write_remote_file(container, content, remote_path, suffix):
    path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', suffix=suffix, delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        # docker cp preserves mode bits but lands root-owned; the pooler
        # must read it regardless of runtime user, so 644 explicitly.
        os.chmod(path, 0o644)
        res = _run(['docker', 'cp', path, f'{container}:{remote_path}'], timeout=60)
        return res
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def push_tenants_config():
    """Render + push tenant pools; restart pooler only when changed.

    Never pushes an unrenderable config: a render failure returns
    ok=False and leaves the running pooler untouched — pushing a bad
    pgbouncer.ini would crash-loop it.
    """
    pools = list_tenant_pools(with_passwords=True)
    try:
        ini, userlist = render_tenants_config(pools)
    except Exception as exc:
        logger.warning("tenant pooler: render failed, pooler untouched: %s", exc)
        return {'ok': False, 'pools': len(pools), 'error': str(exc)[:300]}
    if not ini.strip():
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
    current_ini = _read_remote_file(container, TENANTS_INI_PATH)
    current_ul = _read_remote_file(container, TENANTS_USERLIST_PATH)
    if current_ini == ini and current_ul == userlist:
        return {'ok': True, 'pools': len(pools), 'changed': False, 'restarted': False}
    res = _write_remote_file(container, ini, TENANTS_INI_PATH, '.ini')
    if res.get('error'):
        return {'ok': False, 'pools': len(pools),
                'error': f'config write failed: {res["error"]}'}
    res = _write_remote_file(container, userlist, TENANTS_USERLIST_PATH, '.txt')
    if res.get('error'):
        return {'ok': False, 'pools': len(pools),
                'error': f'userlist write failed: {res["error"]}'}
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
