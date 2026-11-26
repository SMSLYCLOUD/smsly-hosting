"""Addon High-Availability provisioning (Phase 1: REDIS).

Enables automatic-failover replication for Redis addons using only stock
images:

- 1 standby container replicating the primary (``--replicaof``)
- 3 Sentinel containers (quorum=2) that elect/promote a new master
- 1 HAProxy sidecar that holds the addon's friendly network alias and
  routes to whichever backend currently answers ``ROLE`` as ``master``

The stored ``connection_url`` keeps working unchanged across failovers:
the alias moves to the sidecar at enable time and never moves again;
Sentinel does the promoting and HAProxy follows transparently.

Passwords are injected through env files / shell interpolation so they
never appear in ``ps`` output or container ``Config.Cmd``.
"""
import contextlib
import logging
import os
import re
import secrets
import subprocess
import time

from shlex import quote as shlex_quote
from uuid import uuid4

logger = logging.getLogger(__name__)


def _ha_fernet():
    """Return a Fernet instance backed by FIELD_ENCRYPTION_KEY, or None."""
    try:
        from cryptography.fernet import Fernet
        from django.conf import settings
        key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None) or os.environ.get('FIELD_ENCRYPTION_KEY', '')
        if isinstance(key, (list, tuple)):
            key = key[0] if key else ''
        if isinstance(key, bytes):
            key = key.decode('ascii', errors='ignore')
        key = str(key or '').strip()
        if not key:
            return None
        return Fernet(key.encode('ascii'))
    except Exception:
        return None


def _encrypt_ha_secret(secret: str) -> str:
    f = _ha_fernet()
    if not f:
        raise AddonHaError("FIELD_ENCRYPTION_KEY not configured for HA replication credential storage")
    return "enc:" + f.encrypt(secret.encode('utf-8')).decode('ascii')


def _decrypt_ha_secret(token: str) -> str | None:
    if not isinstance(token, str) or not token.startswith("enc:"):
        return None
    f = _ha_fernet()
    if not f:
        return None
    try:
        return f.decrypt(token[4:].encode('ascii')).decode('utf-8')
    except Exception:
        return None

REDIS_IMAGE = 'redis:7-alpine'
HAPROXY_IMAGE = 'haproxy:2.9-alpine'

_SENTINEL_QUORUM = 2
_DOWN_AFTER_MS = 5000
_FAILOVER_TIMEOUT_MS = 15000

_PG_STANDBY_LAG_OK = 10  # seconds of replication delay tolerated before DEGRADED

# Seed-time ownership repair for standby containers: fresh named volumes
# are root-owned 0755 and these seeds bypass the image entrypoint (which
# would fix ownership). Postgres refuses anything but 0700/0750, so
# without this every standby seeded onto a fresh volume crash-loops
# (2026-09-18: shared standby proved it live).
SEED_OWNERSHIP_FIX = (
    "find /var/lib/postgresql/data -mindepth 1 -delete ; "
    "chown postgres:postgres /var/lib/postgresql/data ; "
    "chmod 0700 /var/lib/postgresql/data ; "
)


def _sync_replication_enabled() -> bool:
    """Opt-in synchronous replication for HA Postgres addons.

    ADDON_PG_HA_SYNC_REPLICATION=true eliminates the write-loss window on
    primary failure at the cost of higher commit latency and a hard
    dependency on standby availability (writes block while no standby is
    streaming). Default: async (performance-first).
    """
    import os
    return os.environ.get(
        'ADDON_PG_HA_SYNC_REPLICATION', 'false',
    ).strip().lower() in ('1', 'true', 'yes', 'on')


def _sh(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, check=True, timeout=timeout,
    )


class AddonHaError(RuntimeError):
    """Raised when enabling/disabling addon HA fails midway."""


class AddonHaManager:
    """Provision/inspect/teardown auto-failover topologies for addons."""

    def __init__(self, network_name: str):
        self.network_name = network_name

    # ── naming ────────────────────────────────────────────────────────────

    @staticmethod
    def primary_container(addon) -> str:
        return f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"

    @staticmethod
    def replica_container(addon) -> str:
        return f"{AddonHaManager.primary_container(addon)}-ha-replica"

    @staticmethod
    def sentinel_container(addon, idx: int) -> str:
        return f"{AddonHaManager.primary_container(addon)}-ha-sentinel-{idx}"

    @staticmethod
    def proxy_container(addon) -> str:
        return f"{AddonHaManager.primary_container(addon)}-ha-proxy"

    # ── public API ────────────────────────────────────────────────────────

    def enable_redis_ha(self, addon, password: str) -> dict:
        """Attach standby + sentinel trio + alias-holding proxy to a Redis addon."""
        primary = self.primary_container(addon)
        replica = self.replica_container(addon)
        alias = addon.name
        port = 6379

        self._assert_running(primary)
        self._assert_no_existing_components(addon)
        tls_enabled = self._container_has_mount(primary, '/etc/redis/tls')

        # 1. Standby — replicates the primary by its container name (always
        #    DNS-resolvable on the network, independent of the alias).
        self._run_redis_replica(replica, primary, port, password, tls_enabled=tls_enabled)

        # 2. Sentinel trio — monitors the primary by container name.
        sentinels = []
        for idx in (1, 2, 3):
            name = self.sentinel_container(addon, idx)
            self._run_sentinel(name, primary, port, password, tls_enabled=tls_enabled)
            sentinels.append(name)

        # 3. Proxy sidecar takes over the friendly alias BEFORE we strip it
        #    from the primary, so there is no window without a resolvable
        #    endpoint (both hold it briefly; docker round-robin during this
        #    window still reaches the master because the proxy forwards there).
        proxy = self.proxy_container(addon)
        self._run_proxy(proxy, alias, primary, replica, port, password, tls_enabled=tls_enabled)

        try:
            self._wait_tcp(proxy, port, timeout=45)
            self._wait_tcp(replica, port, timeout=90)

            # 4. Strip the alias from the primary (brief reconnect blip).
            self._move_alias_off(primary, alias)
        except Exception as exc:
            logger.exception("enable_redis_ha(%s) failed", addon.id)
            raise AddonHaError(f"enable_redis_ha failed: {exc}") from exc

        topology = {
            'mode': 'redis-sentinel',
            'primary': primary,
            'replica': replica,
            'sentinels': sentinels,
            'proxy': proxy,
            'quorum': _SENTINEL_QUORUM,
            'network': self.network_name,
        }
        return topology

    def teardown(self, addon) -> list[str]:
        """Remove all HA components, restoring the alias onto the live master."""
        removed: list[str] = []

        if addon.addon_type == 'POSTGRES':
            topology = addon.ha_topology or {}
            current_master, current_standby = self.pg_role_containers(addon)

            if topology.get('placement') == 'remote':
                from apps.deployments.models.core import ManagedServer
                server = ManagedServer.objects.filter(
                    id=topology.get('server_id')).first()
                if server:
                    ssh = self._ssh_client(server)
                    try:
                        ssh.exec_command(f"docker rm -f {shlex_quote(current_standby)}",
                                         timeout=60, raise_on_error=False)
                        ssh.exec_command(
                            f"docker volume rm {shlex_quote(current_standby + '-data')}",
                            timeout=30, raise_on_error=False,
                        )
                        ssh.exec_command(
                            f"docker volume rm {shlex_quote(current_standby + '-tls')} 2>/dev/null",
                            timeout=30, raise_on_error=False,
                        )
                    finally:
                        with contextlib.suppress(Exception):
                            ssh.close()
                    removed.append(current_standby)
                # Local primary keeps serving under its alias already.
                return removed

            # Restore the alias onto whichever container currently serves
            # writes (post-failover that is topology['primary']).
            alias_target = None
            for candidate in (current_master, current_standby):
                if self._container_exists(candidate):
                    probe = self.is_postgres_primary_alive_container(
                        candidate, addon)
                    if probe is True:
                        alias_target = candidate
                        break
                    if alias_target is None:
                        alias_target = candidate
            if alias_target:
                try:
                    self._ensure_alias(alias_target, addon.name)
                except Exception:
                    logger.warning(
                        "teardown(%s): could not restore alias %r on %s",
                        addon.id, addon.name, alias_target, exc_info=True,
                    )
            if self._remove_container(current_standby):
                removed.append(current_standby)
            return removed

        proxy = self.proxy_container(addon)
        replica = self.replica_container(addon)
        components = [proxy]
        components += [self.sentinel_container(addon, i) for i in (1, 2, 3)]
        components.append(replica)

        for name in components:
            if self._remove_container(name):
                removed.append(name)

        # Restore direct reachability under the friendly alias.
        if addon.ha_topology.get('proxy') == proxy:
            primary = self._current_master_container(addon) or self.primary_container(addon)
            try:
                self._ensure_alias(primary, addon.name)
            except Exception:
                logger.warning(
                    "teardown(%s): could not restore alias %r on %s",
                    addon.id, addon.name, primary, exc_info=True,
                )
        return removed

    # ── component runners ────────────────────────────────────────────────

    def _run_redis_replica(self, name: str, primary: str, port: int, password: str,
                           tls_enabled: bool = False) -> None:
        tls_mounts: list[str] = []
        tls_flags = ''
        if tls_enabled:
            from apps.addons.services.addon_tls import ensure_tls_volume
            tls_vol = f'{name}-tls'
            ensure_tls_volume(
                tls_vol,
                name,
                [name, primary, 'localhost'],
                image=REDIS_IMAGE,
                owner='redis',
                target_dir='/certs',
            )
            tls_mounts = ['-v', f'{tls_vol}:/etc/redis/tls:ro']
            tls_flags = (
                f'--tls-port {port} --port 0 --tls-replication yes '
                '--tls-cert-file /etc/redis/tls/server.crt '
                '--tls-key-file /etc/redis/tls/server.key '
                '--tls-ca-cert-file /etc/redis/tls/ca.crt '
                '--tls-auth-clients no '
            )
        cmd = [
            'docker', 'run', '-d', '--name', name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--pids-limit', '1024',
            '--env-file', self._env_file({'REDIS_PASSWORD': password}),
            '-v', f'{name}-data:/data',
            *tls_mounts,
            REDIS_IMAGE,
            'sh', '-c',
            'chown -R redis:redis /data 2>/dev/null; '
            'exec setpriv --reuid redis --regid redis --clear-groups -- '
            'redis-server '
            f'--requirepass "$REDIS_PASSWORD" '
            f'--masterauth "$REDIS_PASSWORD" '
            f'--replicaof {primary} {port} '
            f'{tls_flags}'
            '--appendonly yes --save ""',
        ]
        self._docker_run(cmd, name)

    def _run_sentinel(self, name: str, primary: str, port: int, password: str,
                      tls_enabled: bool = False) -> None:
        # Heredoc (unquoted EOF) so the SHELL expands $SENTINEL_MASTER_PASS
        # into the conf at container start — redis-server does NOT expand
        # env vars in conf files. The secret never appears in Config.Cmd.
        tls_mounts: list[str] = []
        conf_lines_list = [
            'port 0' if tls_enabled else 'port 26379',
            f'sentinel monitor mymaster {primary} {port} {_SENTINEL_QUORUM}',
            'sentinel auth-pass mymaster $SENTINEL_MASTER_PASS',
            f'sentinel down-after-milliseconds mymaster {_DOWN_AFTER_MS}',
            f'sentinel failover-timeout mymaster {_FAILOVER_TIMEOUT_MS}',
            'sentinel parallel-syncs mymaster 1',
            'sentinel resolve-hostnames yes',
            'sentinel announce-hostnames yes',
        ]
        if tls_enabled:
            from apps.addons.services.addon_tls import ensure_tls_volume
            tls_vol = f'{name}-tls'
            ensure_tls_volume(
                tls_vol,
                name,
                [name, primary, 'localhost'],
                image=REDIS_IMAGE,
                owner='redis',
                target_dir='/certs',
            )
            tls_mounts = ['-v', f'{tls_vol}:/etc/redis/tls:ro']
            conf_lines_list += [
                'tls-port 26379',
                'tls-replication yes',
                'tls-cert-file /etc/redis/tls/server.crt',
                'tls-key-file /etc/redis/tls/server.key',
                'tls-ca-cert-file /etc/redis/tls/ca.crt',
                'tls-auth-clients no',
            ]
        heredoc = '\n'.join(conf_lines_list)
        cmd = [
            'docker', 'run', '-d', '--name', name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--pids-limit', '256',
            '--cap-drop=ALL',
            '--env-file', self._env_file({'SENTINEL_MASTER_PASS': password}),
            *tls_mounts,
            REDIS_IMAGE,
            'sh', '-c',
            f'cat > /tmp/sentinel.conf <<EOF\n{heredoc}\nEOF\n'
            'exec redis-server /tmp/sentinel.conf --sentinel',
        ]
        self._docker_run(cmd, name)

    def _run_proxy(self, name: str, alias: str, primary: str,
                   replica: str, port: int, password: str,
                   tls_enabled: bool = False) -> None:
        # Two subtleties handled by the unquoted heredoc:
        # 1. $REDIS_PASSWORD is expanded BY THE SHELL into the cfg (haproxy
        #    does not reliably expand env vars inside quoted tcp-check args).
        # 2. Backslash-r/backslash-n survive as literal characters so
        #    haproxy's own parser turns them into CR/LF. printf '%b' would
        #    have emitted real CRLF bytes mid-directive and broken the file.
        #
        # Role detection uses the raw RESP framing (binary expect of
        # "$6\r\nmaster\r\n") instead of substring matching so a hostname
        # containing the word "master" can never be mistaken for the role.
        master_resp_hex = '24360d0a6d61737465720d0a'
        tls_mounts = ['-v', f'{primary}-tls:/etc/redis/tls:ro'] if tls_enabled else []
        tcp_connect = '    tcp-check connect ssl\n' if tls_enabled else '    tcp-check connect\n'
        srv_tls = ' check-ssl verify required ca-file /etc/redis/tls/ca.crt' if tls_enabled else ''
        heredoc = (
            'defaults\n'
            '    mode tcp\n'
            '    timeout connect 3s\n'
            '    timeout server 30s\n'
            '    timeout client 30s\n'
            'listen redis-master\n'
            f'    bind *:{port}\n'
            '    option tcp-check\n'
            f'{tcp_connect}'
            '    tcp-check send "AUTH $REDIS_PASSWORD\\r\\n"\n'
            '    tcp-check expect string +OK\n'
            '    tcp-check send "ROLE\\r\\n"\n'
            f'    tcp-check expect binary {master_resp_hex}\n'
            '    default-server inter 3s fall 3 rise 2\n'
            f'    server primary {primary}:{port} check{srv_tls}\n'
            f'    server standby {replica}:{port} check{srv_tls}\n'
        )
        cmd = [
            'docker', 'run', '-d', '--name', name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--pids-limit', '256',
            '--cap-drop=ALL',
            '--cap-add=NET_BIND_SERVICE',
            '--env-file', self._env_file({'REDIS_PASSWORD': password}),
            *tls_mounts,
            '--network-alias', alias,
            HAPROXY_IMAGE,
            'sh', '-c',
            f'cat > /tmp/haproxy.cfg <<EOF\n{heredoc}\nEOF\n'
            'exec haproxy -W -db -f /tmp/haproxy.cfg',
        ]
        self._docker_run(cmd, name)

    # ── Postgres (platform-managed auto-failover) ─────────────────────────

    @staticmethod
    def derive_publish_port(addon_id) -> int:
        """Deterministic WG-facing port (20000-29999) for cross-node replication."""
        return 20000 + (int(addon_id.int if hasattr(addon_id, 'int') else addon_id) % 10000)

    def _primary_node(self):
        from apps.deployments.models.core import ManagedServer
        return ManagedServer.objects.filter(is_primary=True).first()

    def _pick_free_port(self, preferred: int) -> int:
        import socket
        for candidate in range(preferred, preferred + 50):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(('0.0.0.0', candidate))
                s.close()
                return candidate
            except OSError:
                continue
            finally:
                with_suppress_close(s)
        raise AddonHaError(f"No free port near {preferred} available on primary host")

    def enable_postgres_ha(self, addon, password: str,
                           placement: str = 'local', remote_server=None) -> dict:
        """Convert a Postgres addon to primary + streaming standby.

        placement='local'  — standby on the same node; watchdog auto-promotes.
        placement='remote' — warm DR standby on another mesh node; the
            primary publishes Postgres on its WireGuard address so the
            standby streams across nodes. Cutover stays manual (the alias
            cannot follow across docker hosts); use promote-ha.
        """
        from apps.addons.services.addon_provisioner import addon_provisioner

        primary = self.primary_container(addon)
        alias = addon.name
        replicator = 'ha_replicator'
        replicator_password = secrets.token_urlsafe(24)

        self._assert_running(primary)
        self._assert_no_existing_components(addon)

        creds = self._parse_pg_url(addon.connection_url)
        db_user, db_name, port = creds['user'], creds['db'], creds['port']

        wg_ip = None
        publish_port = None
        remote_wg_ip = None
        remote_publish_port = None
        if placement == 'remote':
            if remote_server is None or not getattr(remote_server, 'wg_address', None):
                raise AddonHaError(
                    "Remote placement requires a target server with a WireGuard address."
                )
            node = self._primary_node()
            if not node or not getattr(node, 'wg_address', None):
                raise AddonHaError(
                    "Remote placement requires a primary node with a "
                    "WireGuard address."
                )
            wg_ip = node.wg_address
            publish_port = self._pick_free_port(self.derive_publish_port(addon.id))
            remote_wg_ip = remote_server.wg_address
            remote_publish_port = self._pick_free_port(self.derive_publish_port(addon.id) + 1)

        try:
            # 1. Recreate the primary with replication enabled.
            self._recreate_primary_with_replication(
                addon, primary, db_user, db_name, port, password,
                publish=(wg_ip, publish_port) if wg_ip else None)
            self._wait_tcp(primary, port, timeout=120)

            # 2. Replication role + scoped pg_hba rule inside the primary.
            hba_cidr = (
                f"{remote_wg_ip}/32" if placement == 'remote' and remote_wg_ip
                else self._local_hba_subnet(self.network_name or addon_provisioner.network_name)
            )
            self._create_replication_role(primary, db_user, db_name,
                                          replicator, replicator_password,
                                          hba_cidr=hba_cidr)

            if placement == 'remote':
                if remote_server is None:
                    raise AddonHaError("remote placement requires a server")
                standby = f"{primary}-ha-standby"
                self._run_remote_postgres_standby(
                    remote_server, standby, wg_ip, publish_port,
                    replicator, replicator_password,
                    publish=(remote_wg_ip, remote_publish_port) if remote_wg_ip else None)
                self._wait_remote_pg_ready(remote_server, standby, timeout=300)
            else:
                # 3. Seed + start the streaming standby.
                standby = f"{primary}-ha-standby"
                self._run_postgres_standby(
                    standby, primary, port, replicator, replicator_password)
                self._wait_tcp(standby, port, timeout=300)

            self._assert_streaming(primary, db_user)
        except Exception as exc:
            logger.exception("enable_postgres_ha(%s) failed", addon.id)
            raise AddonHaError(f"enable_postgres_ha failed: {exc}") from exc

        topology = {
            'mode': 'postgres-watchdog',
            'placement': placement,
            'primary': primary,
            'standby': standby,
            'replicator_user': replicator,
            'replicator_password': _encrypt_ha_secret(replicator_password),
            'network': self.network_name or addon_provisioner.network_name,
        }
        if placement == 'remote':
            topology.update({
                'server_id': str(getattr(remote_server, 'id', '')),
                'server_host': getattr(remote_server, 'host', ''),
                'replication_endpoint': f'{wg_ip}:{publish_port}',
                'remote_endpoint': f'{remote_wg_ip}:{remote_publish_port}',
            })
        return topology

    def promote_postgres_standby(self, addon) -> str:
        """Promote the standby and move the alias onto it. Returns its name.

        The promotion target is read from ``ha_topology['standby']`` when
        present — after a failover+reseed cycle the roles swap, and the
        derived container name would point at the wrong node.
        """
        topology = getattr(addon, 'ha_topology', None) or {}
        standby = topology.get('standby') \
            or getattr(addon, 'replica_container_name', '') \
            or f"{self.primary_container(addon)}-ha-standby"
        if not self._container_exists(standby):
            raise AddonHaError("No standby container found to promote.")

        result = subprocess.run(
            ['docker', 'exec', standby, 'gosu', 'postgres',
             'pg_ctl', 'promote', '-D', '/var/lib/postgresql/data',
             '-t', '60'],
            capture_output=True, text=True, timeout=90,
        )
        if 'promoted' not in (result.stdout + result.stderr).lower():
            logger.warning("promote output: %s %s", result.stdout, result.stderr)

        # Alias follows promotion so app URLs stay valid.
        self._ensure_alias(standby, addon.name)
        return standby

    def swap_pg_roles(self, addon, new_master: str) -> dict:
        """Record that ``new_master`` is now primary and the old primary
        container is the standby. Returns the updated topology."""
        topology = dict(getattr(addon, 'ha_topology', None) or {})
        old_primary = topology.get('primary') or self.primary_container(addon)
        if new_master == old_primary:
            return topology
        topology['primary'] = new_master
        topology['standby'] = old_primary
        return topology

    def pg_role_containers(self, addon) -> tuple[str, str]:
        """Return (primary_container, standby_container) per stored topology."""
        topology = getattr(addon, 'ha_topology', None) or {}
        primary = topology.get('primary') or self.primary_container(addon)
        standby = topology.get('standby') \
            or f"{self.primary_container(addon)}-ha-standby"
        return primary, standby

    def fence_primary(self, addon, container_name: str | None = None) -> None:
        """Stop the old primary so it cannot resurrect as a rogue master.

        ``--restart unless-stopped`` containers stay stopped after a manual
        stop, so this is durable until teardown/re-enable reseeds it.
        """
        primary = container_name or self.primary_container(addon)
        if not primary or not self._container_exists(primary):
            return
        subprocess.run(
            ['docker', 'stop', '-t', '30', primary],
            capture_output=True, timeout=120,
        )
        logger.info("addon_ha: fenced old primary %s", primary)

    def fence_primary_container(self, addon, old_name: str | None = None) -> None:
        """Alias used by watchdog/views to fence a specific old primary container."""
        self.fence_primary(addon, container_name=old_name)

    def _resolve_replicator_password(self, addon) -> str:
        topology = addon.ha_topology if isinstance(getattr(addon, 'ha_topology', None), dict) else {}
        enc_pw = topology.get('replicator_password')
        if enc_pw:
            decrypted = _decrypt_ha_secret(enc_pw)
            if decrypted:
                return decrypted
        return addon.id.hex[:24]

    def _local_hba_subnet(self, network_name: str) -> str:
        try:
            proc = subprocess.run(
                ['docker', 'network', 'inspect', '-f',
                 '{{range .IPAM.Config}}{{.Subnet}} {{end}}', network_name],
                capture_output=True, text=True, timeout=10,
            )
            subnets = [s for s in (proc.stdout or '').split() if '/' in s]
            if subnets:
                return subnets[0]
        except Exception:
            pass
        return '172.16.0.0/12'

    def reseed_as_standby(self, addon, source_container: str) -> str:
        """Turn the fenced old-primary container into a standby of ``source``.

        Reuses the fenced container name (never the active ``source_container``),
        wipes divergent data, and seeds fresh from the current master.
        """
        from apps.addons.services.addon_provisioner import addon_provisioner

        topology = addon.ha_topology if isinstance(getattr(addon, 'ha_topology', None), dict) else {}
        candidates = [
            topology.get('standby'),
            topology.get('primary'),
            self.primary_container(addon),
        ]
        name = next((c for c in candidates if c and c != source_container), None)
        if not name:
            raise AddonHaError(
                f"Refusing to reseed active master container {source_container!r}"
            )
        replicator = topology.get('replicator_user') or 'ha_replicator'
        replicator_password = self._resolve_replicator_password(addon)

        creds = self._parse_pg_url(addon.connection_url)
        port = creds['port']

        # Capture BEFORE removing the container — mappings die with it.
        host_port = addon_provisioner._get_published_host_port(name)

        subprocess.run(['docker', 'rm', '-f', name],
                       capture_output=True, timeout=60)

        tls_vol = self._ensure_pg_tls_volume(name, alias=getattr(addon, 'name', '') or name)

        env_file = self._env_file({'PGPASSWORD': replicator_password})
        try:
            cmd = [
                'docker', 'run', '-d', '--name', name,
                '--network', self.network_name or addon_provisioner.network_name,
                '--restart', 'unless-stopped',
                *addon_provisioner.SECURITY_OPTS,
                '--env-file', env_file,
                '-v', f'{name}-data:/var/lib/postgresql/data',
                '-v', f'{tls_vol}:/etc/ssl/smsly-addon:ro',
            ]
            if host_port:
                cmd.extend(['-p', f'{host_port}:{port}'])
            if getattr(addon, 'public_domain', None):
                addon_provisioner._append_traefik_labels(
                    cmd, name.replace('.', '-').replace('_', '-'),
                    addon.public_domain, port)
            cmd.extend([
                'pgvector/pgvector:pg16',
                'sh', '-c',
                'until pg_isready -h ' + source_container + ' -p ' + str(port) +
                ' -q; do sleep 2; done; '
                + SEED_OWNERSHIP_FIX +
                'gosu postgres pg_basebackup -h ' + source_container + ' -p ' + str(port) +
                ' -U ' + replicator + ' -D /var/lib/postgresql/data '
                '-Fp -Xs -P -R ; '
                'exec gosu postgres postgres '
                '-c ssl=on '
                '-c ssl_cert_file=/etc/ssl/smsly-addon/server.crt '
                '-c ssl_key_file=/etc/ssl/smsly-addon/server.key '
                '-c ssl_ca_file=/etc/ssl/smsly-addon/ca.crt',
            ])
            self._docker_run(cmd, name)
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                import os
                os.remove(env_file)

        # The alias belongs to the new master now; make sure it stays there.
        try:
            self._move_alias_off(name, addon.name)
        except Exception:
            logger.warning(
                "reseed(%s): alias already on promoted master", addon.id,
                exc_info=True)

        self._assert_streaming(source_container, creds['user'])
        return name

    def _parse_pg_url(self, url: str) -> dict:
        from urllib.parse import urlparse
        parsed = urlparse(url or '')
        return {
            'user': parsed.username or '',
            'password': parsed.password or '',
            'db': (parsed.path or '/').lstrip('/') or 'postgres',
            'port': parsed.port or 5432,
            'query': parsed.query or '',
        }

    def _recreate_primary_with_replication(self, addon, primary: str,
                                           db_user: str, db_name: str,
                                           port: int, password: str,
                                           publish: tuple[str, int] | None = None) -> None:
        """Recreate the primary with wal_level=replica, keeping its volume."""
        from apps.addons.services.addon_provisioner import addon_provisioner

        alias = getattr(addon, 'name', '')
        tls_vol = self._ensure_pg_tls_volume(primary, alias=alias or primary)

        env_file = self._env_file({
            'POSTGRES_PASSWORD': password,
            'POSTGRES_USER': db_user,
            'POSTGRES_DB': db_name,
        })
        try:
            # Capture BEFORE removing the container — the mapping dies with it.
            host_port = addon_provisioner._get_published_host_port(primary)

            subprocess.run(['docker', 'stop', primary],
                           capture_output=True, timeout=90)
            subprocess.run(['docker', 'rm', primary],
                           capture_output=True, timeout=60)

            cmd = [
                'docker', 'run', '-d', '--name', primary,
                '--network', self.network_name or addon_provisioner.network_name,
                '--restart', 'unless-stopped',
                *addon_provisioner.SECURITY_OPTS,
                '--env-file', env_file,
                '-v', f'{primary}-data:/var/lib/postgresql/data',
                '-v', f'{tls_vol}:/etc/ssl/smsly-addon:ro',
            ]
            # The friendly alias MUST stay on the primary — the stored
            # connection_url resolves through it.
            if alias:
                cmd.extend(['--network-alias', alias])
            if host_port:
                cmd.extend(['-p', f'{host_port}:{port}'])
            if publish:
                wg_ip, wg_port = publish
                cmd.extend(['-p', f'{wg_ip}:{wg_port}:{port}'])
            if getattr(addon, 'public_domain', None):
                addon_provisioner._append_traefik_labels(
                    cmd, primary.replace('.', '-').replace('_', '-'),
                    addon.public_domain, port)
            cmd.extend([
                'pgvector/pgvector:pg16',
                '-c', 'wal_level=replica',
                '-c', 'max_wal_senders=10',
                '-c', 'max_replication_slots=8',
                '-c', 'hot_standby=on',
                '-c', 'ssl=on',
                '-c', 'ssl_cert_file=/etc/ssl/smsly-addon/server.crt',
                '-c', 'ssl_key_file=/etc/ssl/smsly-addon/server.key',
                '-c', 'ssl_ca_file=/etc/ssl/smsly-addon/ca.crt',
            ])
            if _sync_replication_enabled():
                # Durability mode: a commit is acknowledged only after the
                # standby has flushed the WAL record (remote_write). Trades
                # write latency and standby-availability for zero write loss
                # on primary failure. FIRST 1 (*) matches any streaming
                # standby regardless of its application_name.
                cmd.extend([
                    '-c', 'synchronous_standby_names=FIRST 1 (*)',
                    '-c', 'synchronous_commit=remote_write',
                ])
            self._docker_run(cmd, primary)
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                import os
                os.remove(env_file)

    def _ssh_client(self, server):
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host,
            key_content=server.ssh_key,
            password=server.ssh_password,
            user=server.ssh_user,
            port=server.ssh_port,
            wg_address=getattr(server, 'wg_address', None),
        )
        ssh.connect()
        return ssh

    def _run_remote_postgres_standby(self, server, name: str, wg_ip: str,
                                     wg_port: int, replicator: str,
                                     replicator_password: str,
                                     publish: tuple[str, int] | None = None) -> None:
        """Seed a streaming standby on a remote mesh node over SSH.

        The standby pulls WAL from the primary node's WireGuard-published
        endpoint, protecting the database against full host loss. Cutover is
        manual (promote-ha) because the docker alias cannot span hosts.
        """
        env_file = self._env_file({'PGPASSWORD': replicator_password})
        ssh = self._ssh_client(server)
        remote_env = f"/tmp/smsly-ha-{uuid4().hex}.env"
        remote_temp_files: list[str] = [remote_env]
        try:
            ssh.upload_file(env_file, remote_env)
            tls_vol = self._ensure_pg_tls_volume(name, alias=name, ssh=ssh)

            seed_cmd = (
                'until pg_isready -h ' + wg_ip + ' -p ' + str(wg_port) +
                ' -q; do sleep 2; done; '
                + SEED_OWNERSHIP_FIX +
                'gosu postgres pg_basebackup -h ' + wg_ip + ' -p ' + str(wg_port) +
                ' -U ' + replicator + ' -D /var/lib/postgresql/data '
                '-Fp -Xs -P -R ; '
                'exec gosu postgres postgres '
                '-c ssl=on '
                '-c ssl_cert_file=/etc/ssl/smsly-addon/server.crt '
                '-c ssl_key_file=/etc/ssl/smsly-addon/server.key '
                '-c ssl_ca_file=/etc/ssl/smsly-addon/ca.crt'
            )
            cmd_parts = [
                'docker', 'run', '-d',
                '--name', name,
                '--network', self.network_name,
                '--restart', 'unless-stopped',
                '--security-opt', 'no-new-privileges:true',
                '--cap-drop=ALL',
                '--cap-add=NET_BIND_SERVICE',
                '--cap-add=CHOWN',
                '--cap-add=SETUID',
                '--cap-add=SETGID',
                '--cap-add=DAC_OVERRIDE',
                '--cap-add=FOWNER',
                '--pids-limit', '1024',
                '--env-file', remote_env,
            ]
            if publish:
                cmd_parts.extend(['-p', f'{publish[0]}:{publish[1]}:5432'])
            cmd_parts.extend([
                '-v', f'{name}-data:/var/lib/postgresql/data',
                '-v', f'{tls_vol}:/etc/ssl/smsly-addon:ro',
                'pgvector/pgvector:pg16',
                'sh', '-c', seed_cmd,
            ])
            cmd_str = ' '.join(shlex_quote(p) for p in cmd_parts)
            out, err, code = ssh.exec_command(cmd_str, timeout=300)
            if code != 0:
                raise AddonHaError(
                    f"Remote standby run failed on {server.host}: {err or out}")
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                import os
                os.remove(env_file)
            for tmp in remote_temp_files:
                with contextlib.suppress(Exception):
                    ssh.exec_command(f"rm -f {shlex_quote(tmp)}", timeout=15,
                                     raise_on_error=False)
            with contextlib.suppress(Exception):
                ssh.close()

    def _wait_remote_pg_ready(self, server, container: str,
                              timeout: int = 300) -> None:
        """Wait until the remote standby accepts connections."""
        ssh = self._ssh_client(server)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                out, _, code = ssh.exec_command(
                    f"docker exec {container} pg_isready -q -h 127.0.0.1",
                    timeout=30, raise_on_error=False,
                )
                if code == 0:
                    return
                time.sleep(3)
            raise AddonHaError(
                f"Remote standby {container} not ready within {timeout}s")
        finally:
            with contextlib.suppress(Exception):
                ssh.close()

    def promote_remote_standby(self, addon) -> dict:
        """Manually cut over to the remote warm standby.

        Promotes the remote container and returns the WireGuard endpoint that
        replaces the stored connection_url host/port (services must pick up
        the new URL on their next deploy).
        """
        from apps.deployments.models.core import ManagedServer

        topology = addon.ha_topology or {}
        server_id = topology.get('server_id')
        standby = getattr(addon, 'replica_container_name', '') \
            or topology.get('standby')
        if not (server_id and standby):
            raise AddonHaError("Addon has no remote standby recorded.")

        server = ManagedServer.objects.filter(id=server_id).first()
        if not server:
            raise AddonHaError("The node hosting the standby no longer exists.")

        ssh = self._ssh_client(server)
        try:
            # Idempotency: if the target is already serving as primary
            # (repeated watchdog cycle / double invocation), skip promote.
            chk_out, _chk_err, chk_code = ssh.exec_command(
                f"docker exec {standby} sh -c "
                f"'gosu postgres psql -U postgres -d postgres "
                f"-tAc \"SELECT pg_is_in_recovery();\"'",
                timeout=30, raise_on_error=False,
            )
            already_master = (
                chk_code == 0 and str(chk_out).strip().lower() == 'f'
            )
            if not already_master:
                out, err, code = ssh.exec_command(
                    f"docker exec {standby} gosu postgres "
                    f"pg_ctl promote -D /var/lib/postgresql/data -t 60",
                    timeout=120,
                )
                if code != 0:
                    raise AddonHaError(
                        f"Remote promotion failed: {err or out}")

            creds = self._parse_pg_url(addon.connection_url)
            endpoint = topology.get('remote_endpoint') or ''
            if endpoint and ':' in endpoint:
                wg_host, _, wg_port = endpoint.partition(':')
            else:
                wg_host = getattr(server, 'wg_address', '') or ''
                wg_port = str(creds['port'])
                if not wg_host:
                    fallback_ep = topology.get('replication_endpoint') or ''
                    wg_host, _, wg_port = fallback_ep.partition(':')

            # Fence the dead local primary if reachable.
            subprocess.run(['docker', 'stop', '-t', '30',
                            self.primary_container(addon)],
                           capture_output=True, timeout=90)

            new_url = (
                f"postgresql://{creds['user']}:{creds['password']}"
                f"@{wg_host}:{wg_port}/{creds['db']}"
                + (f"?{creds['query']}" if creds.get('query') else "")
            )
            return {
                'standby': standby,
                'connection_url': new_url,
                'host': wg_host,
                'port': int(wg_port or creds['port']),
            }
        finally:
            with contextlib.suppress(Exception):
                ssh.close()

    def _create_replication_role(self, primary: str, db_user: str, db_name: str,
                                 replicator: str, replicator_password: str,
                                 hba_cidr: str = '172.16.0.0/12') -> None:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', replicator):
            raise AddonHaError(f"Invalid replicator role name: {replicator!r}")
        tag = "rpw_" + uuid4().hex[:8]
        role_sql = (
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{replicator}') THEN "
            f"CREATE ROLE {replicator} REPLICATION LOGIN PASSWORD "
            f"${tag}${replicator_password}${tag}$; "
            "ELSE "
            f"ALTER ROLE {replicator} WITH PASSWORD ${tag}${replicator_password}${tag}$; "
            "END IF; END $$;"
        )
        subprocess.run(
            ['docker', 'exec', primary, 'psql', '-U', db_user, '-d', db_name,
             '-c', role_sql],
            capture_output=True, text=True, check=True, timeout=60,
        )
        hba_line = f'hostssl replication {replicator} {hba_cidr} scram-sha-256'
        subprocess.run(
            ['docker', 'exec', primary, 'sh', '-c',
             'HBA="${PGDATA:-/var/lib/postgresql/data}/pg_hba.conf"; '
             f'sed -i -E "s/^host([[:space:]]+replication[[:space:]]+{replicator})/hostssl\\1/" "$HBA" 2>/dev/null || true; '
             f'grep -q "hostssl replication {replicator}" "$HBA" || '
             f'echo "{hba_line}" >> "$HBA"'],
            capture_output=True, text=True, check=True, timeout=30,
        )
        subprocess.run(
            ['docker', 'exec', primary, 'psql', '-U', db_user, '-d', db_name,
             '-c', 'SELECT pg_reload_conf();'],
            capture_output=True, text=True, check=True, timeout=30,
        )

    def _run_postgres_standby(self, name: str, primary: str, port: int,
                               replicator: str, replicator_password: str) -> None:
        """Seed a hot standby from the primary via pg_basebackup -R."""
        from apps.addons.services.addon_provisioner import addon_provisioner

        tls_vol = self._ensure_pg_tls_volume(name, alias=primary)
        env_file = self._env_file({'PGPASSWORD': replicator_password})
        try:
            cmd = [
                'docker', 'run', '-d', '--name', name,
                '--network', self.network_name,
                '--restart', 'unless-stopped',
                *addon_provisioner.SECURITY_OPTS,
                '--env-file', env_file,
                '-v', f'{name}-data:/var/lib/postgresql/data',
                '-v', f'{tls_vol}:/etc/ssl/smsly-addon:ro',
                'pgvector/pgvector:pg16',
                'sh', '-c',
                'until pg_isready -h ' + primary + ' -p ' + str(port) +
                ' -q; do sleep 2; done; '
                + SEED_OWNERSHIP_FIX +
                'gosu postgres pg_basebackup -h ' + primary + ' -p ' + str(port) +
                ' -U ' + replicator + ' -D /var/lib/postgresql/data '
                '-Fp -Xs -P -R ; '
                'exec gosu postgres postgres '
                '-c ssl=on '
                '-c ssl_cert_file=/etc/ssl/smsly-addon/server.crt '
                '-c ssl_key_file=/etc/ssl/smsly-addon/server.key '
                '-c ssl_ca_file=/etc/ssl/smsly-addon/ca.crt',
            ]
            self._docker_run(cmd, name)
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                import os
                os.remove(env_file)

    def _assert_streaming(self, primary: str, db_user: str) -> None:
        """Verify at least one standby is streaming from the primary."""
        deadline = time.time() + 60
        query = ("SELECT count(*) FROM pg_stat_replication "
                 "WHERE state = 'streaming';")
        while time.time() < deadline:
            result = subprocess.run(
                ['docker', 'exec', primary, 'psql', '-U', db_user,
                 '-d', 'postgres', '-tAc', query],
                capture_output=True, text=True, timeout=30,
            )
            try:
                if int(result.stdout.strip() or '0') >= 1:
                    return
            except ValueError:
                pass
            time.sleep(3)
        raise AddonHaError("Standby did not reach streaming state within 60s")

    def is_postgres_primary_alive(self, addon) -> bool | None:
        """True = primary serving writes; False = down/in recovery; None = unknown."""
        primary, _ = self.pg_role_containers(addon)
        return self.is_postgres_primary_alive_container(primary, addon)

    def is_postgres_primary_alive_container(self, container: str, addon) -> bool | None:
        """Probe a specific data container for master role and liveness."""
        creds = self._parse_pg_url(addon.connection_url)
        result = subprocess.run(
            ['docker', 'exec', container, 'psql', '-U', creds['user'],
             '-d', 'postgres', '-tAc', 'SELECT pg_is_in_recovery();'],
            capture_output=True, text=True, timeout=15,
        )
        out = result.stdout.strip().lower()
        if out == 'f':
            return True
        if out == 't':
            return False
        return None

    def mark_cutover_done(self, addon) -> dict:
        """Flag the topology so post-cutover cycles check the REMOTE node."""
        topology = dict(getattr(addon, 'ha_topology', None) or {})
        topology['cutover_done'] = True
        type(addon).objects.filter(pk=addon.pk).update(ha_topology=topology)
        return topology

    def remote_standby_healthy(self, addon) -> bool | None:
        """Post-cutover remote health: is the promoted node serving writes?"""
        from apps.deployments.models.core import ManagedServer

        topology = getattr(addon, 'ha_topology', None) or {}
        server_id = topology.get('server_id')
        master = topology.get('primary') or topology.get('standby')
        server = ManagedServer.objects.filter(id=server_id).first() \
            if server_id else None
        if not (server and master):
            return None
        ssh = None
        try:
            ssh = self._ssh_client(server)
            _out, _err, code = ssh.exec_command(
                f"docker inspect -f '{{{{.State.Running}}}}' {shlex_quote(master)}",
                timeout=30, raise_on_error=False,
            )
            if code != 0:
                return False
            creds = self._parse_pg_url(addon.connection_url)
            out, _err2, code2 = ssh.exec_command(
                f"docker exec {master} sh -c "
                f"'psql -U {shlex_quote(str(creds.get('user') or 'postgres'))} "
                f"-d postgres -tAc \"SELECT pg_is_in_recovery();\"'",
                timeout=30, raise_on_error=False,
            )
            if code2 != 0:
                return False
            out_l = str(out).strip().lower()
            return True if out_l == 'f' else (False if out_l == 't' else None)
        except Exception:
            return None
        finally:
            with contextlib.suppress(Exception):
                if ssh:
                    ssh.close()

    # ── helpers ───────────────────────────────────────────────────────────

    def _docker_run(self, cmd: list[str], name: str) -> str:
        result = _sh(cmd)
        cid = result.stdout.strip()[:12]
        logger.info("addon_ha: started %s (%s)", name, cid)
        return cid

    @staticmethod
    def _env_file(env_vars: dict[str, str]) -> str:
        import os
        import tempfile
        fd, path = tempfile.mkstemp(prefix='smsly-ha-', suffix='.env')
        with os.fdopen(fd, 'w') as fh:
            for k, v in env_vars.items():
                fh.write(f'{k}={v}\n')
        return path

    def _assert_running(self, container: str) -> None:
        result = subprocess.run(
            ['docker', 'inspect', '-f', '{{.State.Running}}', container],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip() != 'true':
            raise AddonHaError(
                f"Primary container {container!r} is not running; fix it before enabling HA."
            )

    def _assert_no_existing_components(self, addon) -> None:
        if addon.addon_type == 'POSTGRES':
            leftovers = [
                c for c in (f"{self.primary_container(addon)}-ha-standby",)
                if self._container_exists(c)
            ]
        else:
            leftovers = [
                c for c in (
                    self.proxy_container(addon),
                    self.replica_container(addon),
                    *(self.sentinel_container(addon, i) for i in (1, 2, 3)),
                ) if self._container_exists(c)
            ]
        if leftovers:
            raise AddonHaError(
                f"Stale HA components already exist: {', '.join(leftovers)}. "
                "Run disable-ha first."
            )

    def _container_exists(self, name: str) -> bool:
        result = subprocess.run(
            ['docker', 'inspect', name],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0

    def _ensure_pg_tls_volume(self, name: str, alias: str = '', ssh=None) -> str:
        from apps.addons.services.addon_tls import ensure_tls_volume

        tls_vol = f'{name}-tls'
        dns = [d for d in (alias, name, 'localhost') if d]
        return ensure_tls_volume(
            tls_vol,
            alias or name,
            dns,
            image='pgvector/pgvector:pg16',
            owner='postgres',
            target_dir='/certs',
            ssh=ssh,
        )

    def _container_has_mount(self, container: str, mount_dest: str) -> bool:
        try:
            proc = subprocess.run(
                ['docker', 'inspect', '-f',
                 '{{range .Mounts}}{{.Destination}} {{end}}', container],
                capture_output=True, text=True, timeout=15,
            )
            return proc.returncode == 0 and mount_dest in (proc.stdout or '').split()
        except Exception:
            return False

    def _remove_container(self, name: str) -> bool:
        if not self._container_exists(name):
            return False
        subprocess.run(['docker', 'stop', name], capture_output=True, timeout=60)
        subprocess.run(['docker', 'rm', name], capture_output=True, timeout=60)
        subprocess.run(
            ['docker', 'volume', 'rm', f'{name}-data'], capture_output=True, timeout=60,
        )
        subprocess.run(
            ['docker', 'volume', 'rm', f'{name}-tls'], capture_output=True, timeout=60,
        )
        logger.info("addon_ha: removed %s", name)
        return True

    def _wait_tcp(self, host: str, port: int, timeout: int = 60) -> None:
        import socket
        start = time.time()
        while time.time() - start < timeout:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            try:
                s.connect((host, port))
                s.close()
                return
            except OSError:
                with_suppress_close(s)
                time.sleep(1)
        raise AddonHaError(f"{host}:{port} not reachable within {timeout}s")

    def _current_master_container(self, addon) -> str | None:
        """Ask each data container for its ROLE; return the one answering master."""
        for candidate in (self.primary_container(addon), self.replica_container(addon)):
            result = subprocess.run(
                ['docker', 'exec', candidate, 'sh', '-c',
                 'TLS_ARGS=""; '
                 '[ -f /etc/redis/tls/ca.crt ] && TLS_ARGS="--tls --cacert /etc/redis/tls/ca.crt"; '
                 'redis-cli $TLS_ARGS -a "$REDIS_PASSWORD" ROLE 2>/dev/null | head -n1'],
                capture_output=True, text=True, timeout=15,
            )
            if result.stdout.strip().startswith('master'):
                return candidate
        return None

    def _move_alias_off(self, container: str, alias: str) -> None:
        net = self._container_network(container)
        subprocess.run(
            ['docker', 'network', 'disconnect', net, container],
            capture_output=True, text=True, check=True, timeout=30,
        )
        subprocess.run(
            ['docker', 'network', 'connect', net, container],
            capture_output=True, text=True, check=True, timeout=30,
        )
        logger.info("addon_ha: alias %r moved off %s", alias, container)

    def _ensure_alias(self, container: str, alias: str) -> None:
        net = self._container_network(container)
        subprocess.run(
            ['docker', 'network', 'disconnect', net, container],
            capture_output=True, text=True, check=True, timeout=30,
        )
        subprocess.run(
            ['docker', 'network', 'connect', '--alias', alias, net, container],
            capture_output=True, text=True, check=True, timeout=30,
        )

    def _container_network(self, container: str) -> str:
        result = subprocess.run(
            ['docker', 'inspect', '-f',
             '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}',
             container],
            capture_output=True, text=True, check=True, timeout=15,
        )
        nets = result.stdout.split()
        if not nets:
            raise AddonHaError(f"No network found on container {container!r}")
        return nets[0]


def with_suppress_close(sock) -> None:
    try:
        sock.close()
    except OSError:
        pass
