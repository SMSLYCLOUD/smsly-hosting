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
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

REDIS_IMAGE = 'redis:7-alpine'
HAPROXY_IMAGE = 'haproxy:2.9-alpine'

_SENTINEL_QUORUM = 2
_DOWN_AFTER_MS = 5000
_FAILOVER_TIMEOUT_MS = 15000

_PG_STANDBY_LAG_OK = 10  # seconds of replication delay tolerated before DEGRADED


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

        # 1. Standby — replicates the primary by its container name (always
        #    DNS-resolvable on the network, independent of the alias).
        self._run_redis_replica(replica, primary, port, password)

        # 2. Sentinel trio — monitors the primary by container name.
        sentinels = []
        for idx in (1, 2, 3):
            name = self.sentinel_container(addon, idx)
            self._run_sentinel(name, primary, port, password)
            sentinels.append(name)

        # 3. Proxy sidecar takes over the friendly alias BEFORE we strip it
        #    from the primary, so there is no window without a resolvable
        #    endpoint (both hold it briefly; docker round-robin during this
        #    window still reaches the master because the proxy forwards there).
        proxy = self.proxy_container(addon)
        self._run_proxy(proxy, alias, primary, replica, port, password)

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

    def _run_redis_replica(self, name: str, primary: str, port: int, password: str) -> None:
        cmd = [
            'docker', 'run', '-d', '--name', name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--pids-limit', '1024',
            '--env-file', self._env_file({'REDIS_PASSWORD': password}),
            '-v', f'{name}-data:/data',
            REDIS_IMAGE,
            'sh', '-c',
            'exec redis-server '
            f'--requirepass "$REDIS_PASSWORD" '
            f'--masterauth "$REDIS_PASSWORD" '
            f'--replicaof {primary} {port} '
            '--appendonly yes --save ""',
        ]
        self._docker_run(cmd, name)

    def _run_sentinel(self, name: str, primary: str, port: int, password: str) -> None:
        conf = (
            'port 26379\n'
            f'sentinel monitor mymaster {primary} {port} {_SENTINEL_QUORUM}\n'
            f'sentinel auth-pass mymaster "$SENTINEL_MASTER_PASS"\n'
            f'sentinel down-after-milliseconds mymaster {_DOWN_AFTER_MS}\n'
            f'sentinel failover-timeout mymaster {_FAILOVER_TIMEOUT_MS}\n'
            'sentinel parallel-syncs mymaster 1\n'
            'sentinel resolve-hostnames yes\n'
            'sentinel announce-hostnames yes\n'
        )
        cmd = [
            'docker', 'run', '-d', '--name', name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--pids-limit', '256',
            '--cap-drop=ALL',
            '--env-file', self._env_file({'SENTINEL_MASTER_PASS': password}),
            REDIS_IMAGE,
            'sh', '-c',
            f'printf "%b" \'{conf}\' > /tmp/sentinel.conf && '
            'exec redis-server /tmp/sentinel.conf --sentinel',
        ]
        self._docker_run(cmd, name)

    def _run_proxy(self, name: str, alias: str, primary: str,
                   replica: str, port: int, password: str) -> None:
        cfg = (
            'defaults\n'
            '    mode tcp\n'
            '    timeout connect 3s\n'
            '    timeout server 30s\n'
            '    timeout client 30s\n'
            'listen redis-master\n'
            f'    bind *:{port}\n'
            '    option tcp-check\n'
            '    tcp-check connect\n'
            '    tcp-check send "AUTH "$REDIS_PASSWORD"\\r\\n"\n'
            '    tcp-check expect string +OK\n'
            '    tcp-check send "ROLE\\r\\n"\n'
            '    tcp-check expect string master\n'
            '    default-server inter 3s fall 3 rise 2\n'
            f'    server primary {primary}:{port} check\n'
            f'    server standby {replica}:{port} check\n'
        )
        cmd = [
            'docker', 'run', '-d', '--name', name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--pids-limit', '256',
            '--cap-drop=ALL',
            '--cap-add=NET_BIND_SERVICE',
            '--env-file', self._env_file({'REDIS_PASSWORD': password}),
            '--network-alias', alias,
            HAPROXY_IMAGE,
            'sh', '-c',
            f'printf "%b" \'{cfg}\' > /tmp/haproxy.cfg && '
            'exec haproxy -W -db -f /tmp/haproxy.cfg',
        ]
        self._docker_run(cmd, name)

    # ── Postgres (platform-managed auto-failover) ─────────────────────────

    def enable_postgres_ha(self, addon, password: str) -> dict:
        """Convert a Postgres addon to primary + streaming standby.

        The primary container is recreated with replication settings
        (wal_level=replica) while reusing its data volume and published port —
        one brief downtime window. The standby is seeded via pg_basebackup -R
        and streams WAL continuously. Failover is executed by the HA watchdog
        task: promote the standby, then move the friendly alias so the stored
        connection URL keeps working unchanged.
        """
        from apps.addons.services.addon_provisioner import addon_provisioner

        primary = self.primary_container(addon)
        standby = f"{primary}-ha-standby"
        alias = addon.name
        replicator = 'ha_replicator'
        replicator_password = addon.id.hex[:24]  # per-addon, rotation-safe

        self._assert_running(primary)
        self._assert_no_existing_components(addon)

        creds = self._parse_pg_url(addon.connection_url)
        db_user, db_name, port = creds['user'], creds['db'], creds['port']

        try:
            # 1. Recreate the primary with replication enabled.
            self._recreate_primary_with_replication(
                addon, primary, db_user, db_name, port, password)
            self._wait_tcp(primary, port, timeout=120)

            # 2. Replication role + pg_hba rule inside the primary.
            self._create_replication_role(primary, db_user, db_name,
                                          replicator, replicator_password)

            # 3. Seed + start the streaming standby.
            self._run_postgres_standby(
                standby, primary, port, replicator, replicator_password)
            self._wait_tcp(standby, port, timeout=300)
            self._assert_streaming(primary, db_user)

            # 4. Move the friendly alias to the standby? No — the PRIMARY
            #    keeps the alias in normal operation; the watchdog moves it
            #    to the promoted standby only when the primary dies.
        except Exception as exc:
            logger.exception("enable_postgres_ha(%s) failed", addon.id)
            raise AddonHaError(f"enable_postgres_ha failed: {exc}") from exc

        topology = {
            'mode': 'postgres-watchdog',
            'primary': primary,
            'standby': standby,
            'replicator_user': replicator,
            'network': self.network_name or addon_provisioner.network_name,
        }
        return topology

    def promote_postgres_standby(self, addon) -> str:
        """Promote the standby and move the alias onto it. Returns its name."""
        standby = self.replica_container(addon)
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

    def _parse_pg_url(self, url: str) -> dict:
        from urllib.parse import urlparse
        parsed = urlparse(url or '')
        return {
            'user': parsed.username or '',
            'password': parsed.password or '',
            'db': (parsed.path or '/').lstrip('/') or 'postgres',
            'port': parsed.port or 5432,
        }

    def _recreate_primary_with_replication(self, addon, primary: str,
                                           db_user: str, db_name: str,
                                           port: int, password: str) -> None:
        """Recreate the primary with wal_level=replica, keeping its volume."""
        from apps.addons.services.addon_provisioner import addon_provisioner

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
            ]
            if host_port:
                cmd.extend(['-p', f'{host_port}:{port}'])
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
            ])
            self._docker_run(cmd, primary)
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                import os
                os.remove(env_file)

    def _create_replication_role(self, primary: str, db_user: str, db_name: str,
                                 replicator: str, replicator_password: str) -> None:
        role_sql = (
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{replicator}') THEN "
            f"CREATE ROLE {replicator} REPLICATION LOGIN PASSWORD "
            f"'{replicator_password}'; END IF; END $$;"
        )
        subprocess.run(
            ['docker', 'exec', primary, 'psql', '-U', db_user, '-d', db_name,
             '-c', role_sql],
            capture_output=True, text=True, check=True, timeout=60,
        )
        hba_line = 'host replication ha_replicator 0.0.0.0/0 scram-sha-256\n'
        subprocess.run(
            ['docker', 'exec', primary, 'sh', '-c',
             f'grep -q "host replication {replicator}" '
             '$PGDATA/pg_hba.conf || echo '
             f"'{hba_line}' >> $PGDATA/pg_hba.conf"],
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
        env_file = self._env_file({'PGPASSWORD': replicator_password})
        try:
            cmd = [
                'docker', 'run', '-d', '--name', name,
                '--network', self.network_name,
                '--restart', 'unless-stopped',
                *self.SECURITY_OPTS,
                '--env-file', env_file,
                '-v', f'{name}-data:/var/lib/postgresql/data',
                'pgvector/pgvector:pg16',
                'sh', '-c',
                'until pg_isready -h ' + primary + ' -p ' + str(port) +
                ' -q; do sleep 2; done; '
                'rm -rf /var/lib/postgresql/data/* ; '
                'gosu postgres pg_basebackup -h ' + primary + ' -p ' + str(port) +
                ' -U ' + replicator + ' -D /var/lib/postgresql/data '
                '-Fp -Xs -P -R ; '
                'exec gosu postgres postgres',
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
        primary = self.primary_container(addon)
        creds = self._parse_pg_url(addon.connection_url)
        result = subprocess.run(
            ['docker', 'exec', primary, 'psql', '-U', creds['user'],
             '-d', 'postgres', '-tAc', 'SELECT pg_is_in_recovery();'],
            capture_output=True, text=True, timeout=15,
        )
        out = result.stdout.strip().lower()
        if out == 'f':
            return True
        if out == 't':
            return False
        return None

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

    def _remove_container(self, name: str) -> bool:
        if not self._container_exists(name):
            return False
        subprocess.run(['docker', 'stop', name], capture_output=True, timeout=60)
        subprocess.run(['docker', 'rm', name], capture_output=True, timeout=60)
        subprocess.run(
            ['docker', 'volume', 'rm', f'{name}-data'], capture_output=True, timeout=60,
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
                 'redis-cli -a "$REDIS_PASSWORD" ROLE 2>/dev/null | head -n1'],
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
