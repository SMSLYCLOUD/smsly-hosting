"""Shared Postgres addon server: one instance, N logical databases.

Today every POSTGRES addon is a dedicated container (+volume, +optional
standby pair). For an ecosystem of ~32 microservice databases that is
32+ Postgres processes fighting for RAM on one host. This module hosts
new Postgres addons as logical databases (role + database) on a single
dedicated shared instance instead:

* ``smsly-shared-postgres`` (pgvector/pg16, same image as addon DBs so
  the ``vector`` extension is available), one data volume, ``smsly-net``
  + per-service scoped bridges with the same friendly alias apps
  already dial (``postgres-myapp``) — zero app URL changes.
* Per-addon role: LOGIN, no CREATEDB, ``CONNECTION LIMIT``,
  ``statement_timeout``; ``CONNECT`` revoked from ``PUBLIC`` on each
  new database and granted only to its owner, so tenants cannot open
  each other's databases (or list-then-open — names remain visible in
  the catalog, contents are not).
* Connection math: ``max_connections=400`` on the instance,
  10 per role. Thirty-two tenants peak at 320 + reserved headroom.
  pgcat pooling in front is the follow-up when pools, not processes,
  become the ceiling.

What this deliberately does NOT do:
* Per-addon standby containers (``addon_ha`` stays wired to legacy
  container addons only). Durability for logical DBs is per-DB dumps
  (existing backup path, branched to the shared host) until a shared
  standby is provisioned.
* pgcat routing (direct connections; pool-per-tenant comes later).

All docker/SQL I/O goes through small helpers so unit tests can mock
at the subprocess boundary. Every mutating SQL is idempotent — safe
under ecosystem-wave concurrency and provision retries.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

SHARED_CONTAINER = "smsly-shared-postgres"
SHARED_VOLUME = "smsly-shared-postgres-data"
SHARED_IMAGE = "pgvector/pgvector:pg16"
SHARED_PORT = 5432
SHARED_MAX_CONNECTIONS = 400
SHARED_RESERVED_CONNECTIONS = 10
# Passed as `postgres -c` flags on BOTH primary and standby: command-line
# settings do not replicate, and recovery refuses to start when the
# standby's max_connections is lower than the primary's (observed live).
POSTGRES_TUNING_FLAGS = [
    "-c", f"max_connections={SHARED_MAX_CONNECTIONS}",
    "-c", f"superuser_reserved_connections={SHARED_RESERVED_CONNECTIONS}",
]
ROLE_CONNECTION_LIMIT = 10
ROLE_STATEMENT_TIMEOUT = "30s"

# Streaming standby for the shared instance (one replica covers all
# logical tenants — the N-standby equivalent of per-addon HA).
SHARED_STANDBY = "smsly-shared-postgres-replica"
SHARED_STANDBY_VOLUME = "smsly-shared-postgres-replica-data"
REPLICATOR_ROLE = "shared_replicator"


def _quote_ident(name: str) -> str:
    """Quote an SQL identifier (role / database name)."""
    return '"' + str(name).replace('"', '""') + '"'


def _superuser_password() -> str:
    """Stored superuser password for the shared instance (generated once)."""
    from apps.deployments.models.platform import PlatformConfig

    cfg = PlatformConfig.load()
    password = str(getattr(cfg, "shared_postgres_password", "") or "")
    if not password:
        import secrets

        password = secrets.token_urlsafe(48)
        cfg.shared_postgres_password = password
        cfg.save(update_fields=["shared_postgres_password", "updated_at"])
    return password


def _write_env_file(mapping: dict[str, str]) -> str:
    fd, path = tempfile.mkstemp(prefix="smsly-shared-pg-", suffix=".env", text=True)
    with os.fdopen(fd, "w") as fh:
        for key, value in mapping.items():
            fh.write(f"{key}={value}\n")
    return path


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _container_exists() -> str | None:
    """Container id when the shared server exists (any state), else None."""
    proc = _run(
        ["docker", "ps", "-a", "--filter", f"name=^{SHARED_CONTAINER}$",
         "--format", "{{.ID}}"],
        timeout=30,
    )
    cid = (proc.stdout or "").strip().splitlines()
    return cid[0].strip()[:64] if cid and cid[0].strip() else None


def _container_running() -> bool:
    proc = _run(
        ["docker", "ps", "--filter", f"name=^{SHARED_CONTAINER}$",
         "--format", "{{.ID}}"],
        timeout=30,
    )
    return bool((proc.stdout or "").strip())


def _wait_ready(timeout: int = 120) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = _run(
            ["docker", "exec", SHARED_CONTAINER,
             "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
            timeout=15,
        )
        if proc.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"{SHARED_CONTAINER} never became ready")


def ensure_shared_server() -> str:
    """Create (once) and start the shared Postgres instance.

    Returns the container id. Safe under concurrency: a lost
    create-race surfaces as "already in use" and resolves to the
    winner's container. Never raises for an already-correct setup.
    """
    password = _superuser_password()
    cid = _container_exists()
    if cid is None:
        env_file = _write_env_file({"POSTGRES_PASSWORD": password})
        try:
            proc = _run(
                ["docker", "run", "-d",
                 "--name", SHARED_CONTAINER,
                 "--network", "smsly-net",
                 "--restart", "unless-stopped",
                 "--env-file", env_file,
                 "-v", f"{SHARED_VOLUME}:/var/lib/postgresql/data",
                 SHARED_IMAGE,
                 "postgres",
                 *POSTGRES_TUNING_FLAGS],
                timeout=180,
            )
            if proc.returncode != 0 and "already in use" not in (proc.stderr or ""):
                raise RuntimeError(f"shared postgres create failed: {(proc.stderr or '').strip()[:200]}")
            cid = _container_exists()
            if not cid:
                raise RuntimeError("shared postgres container missing after create")
            logger.info("Created shared Postgres addon server (%s)", cid[:12])
        finally:
            try:
                os.remove(env_file)
            except OSError:
                pass
    if not _container_running():
        _run(["docker", "start", SHARED_CONTAINER], timeout=60)
    _wait_ready()
    # pgvector in template1 so every future database inherits it
    # (matches per-addon behavior of CREATE EXTENSION per database).
    _psql("template1", "CREATE EXTENSION IF NOT EXISTS vector;")
    # Persist tuning into the config file as well: the -c flags win at
    # boot, but a bare file default (max_connections=100) makes every
    # SIGHUP log scary "cannot be changed without restart" warnings, and
    # basebackup copies the file to standbys (2026-09-18 drill).
    # Idempotent; reload failure is non-fatal (boot flags govern).
    try:
        _psql("postgres",
              f"ALTER SYSTEM SET max_connections = '{SHARED_MAX_CONNECTIONS}';")
        _psql("postgres",
              "ALTER SYSTEM SET superuser_reserved_connections = "
              f"'{SHARED_RESERVED_CONNECTIONS}';")
        _psql("postgres", "SELECT pg_reload_conf();")
    except Exception:
        pass
    _harden_system_catalogs()
    return cid


def _harden_system_catalogs() -> None:
    """Revoke PUBLIC connect on system databases (instance-wide, once).

    Per-role REVOKEs are not enough: Postgres evaluates the PUBLIC
    grant for every role, so a tenant keeps connecting until PUBLIC
    itself loses CONNECT. Superusers (our admin ops) and passwordless
    ``pg_isready`` bypass privilege checks, so nothing legitimate
    breaks. Idempotent.
    """
    for sysdb in ("postgres", "template1"):
        try:
            _psql("postgres", f"REVOKE CONNECT ON DATABASE {_quote_ident(sysdb)} FROM PUBLIC;")
        except Exception:
            pass


# ── Streaming standby (shared HA) ───────────────────────────────────
# One replica covers all logical tenants. Mirrors the per-addon HA
# seeding pattern (pg_basebackup -R), minus per-addon topology: the
# replicator credential derives deterministically from the stored
# superuser password, so reseeds never need new persisted secrets.


def _replicator_password() -> str:
    import hashlib

    return hashlib.sha256(
        ("shared-replicator:" + _superuser_password()).encode()
    ).hexdigest()[:32]


def _ensure_replication_access() -> None:
    """Create the replicator role + pg_hba rule on the primary (idempotent)."""
    _ensure_replication_access_on(SHARED_CONTAINER)


def _standby_running() -> bool:
    proc = _run(
        ["docker", "ps", "--filter", f"name=^{SHARED_STANDBY}$",
         "--format", "{{.ID}}"],
        timeout=30,
    )
    return bool((proc.stdout or "").strip())


def _wait_container_ready(container: str, timeout: int = 300) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = _run(
            ["docker", "exec", container,
             "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
            timeout=15,
        )
        if proc.returncode == 0:
            return
        time.sleep(3)
    raise RuntimeError(f"{container} never became ready")


def _primary_conninfo_ok(container: str) -> bool:
    """True when ``container`` streams from the shared primary."""
    try:
        out = _psql("postgres", "SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming';")
        return int((out.splitlines() or ["0"])[0].strip() or 0) >= 1
    except Exception:
        return False


def _volume_users(volume: str) -> list[str]:
    """Names of containers (any state) currently using ``volume``."""
    proc = _run(
        ["docker", "ps", "-a", "--filter", f"volume={volume}",
         "--format", "{{.Names}}"],
        timeout=30,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _gc_old_standby_volumes(current: str) -> None:
    """Remove unreferenced previous reseed volumes (best effort)."""
    proc = _run(
        ["docker", "volume", "ls", "--filter", "name=smsly-shared-postgres-replica-data",
         "--format", "{{.Name}}"],
        timeout=30,
    )
    if proc.returncode != 0:
        return
    for name in [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]:
        if name == current:
            continue
        if _volume_users(name):
            continue
        _run(["docker", "volume", "rm", name], timeout=60)


def ensure_shared_standby(reseed: bool = False) -> str:
    """Create (once) and start the streaming standby. Idempotent.

    Safe under concurrency (lost create-race resolves to the winner)
    and safe to re-run (an existing standby is left alone — never
    re-seeded implicitly, since reseeding wipes data).

    ``reseed=True`` (post-failover): the old standby data sits on a
    forked timeline and can never resume, so the standby gets a FRESH
    timestamped volume. Reusing the canonical volume name is forbidden
    here: after a promote+rename that volume belongs to the live
    primary, and seeding into it wipes the primary's data directory
    (2026-09-18 drill: full primary wipe, recovered from the fenced
    copy). Previous reseed volumes are garbage-collected once the new
    standby streams.
    """
    import time as _time

    ensure_shared_server()
    _ensure_replication_access()
    volume = SHARED_STANDBY_VOLUME
    if reseed:
        _run(["docker", "rm", "-f", SHARED_STANDBY], timeout=120)
        volume = f"{SHARED_STANDBY_VOLUME}-{int(_time.time())}"
        logger.info("shared postgres: reseeding standby on fresh volume %s", volume)
    else:
        # Fail closed: never seed into a volume a live container needs.
        users = [u for u in _volume_users(volume) if u != SHARED_STANDBY]
        if users:
            raise RuntimeError(
                f"standby volume {volume} in use by {users}; refusing to seed "
                "(re-run with reseed=True after failover, or stop the holder).")
    proc = _run(
        ["docker", "ps", "-a", "--filter", f"name=^{SHARED_STANDBY}$",
         "--format", "{{.ID}}"],
        timeout=30,
    )
    if not (proc.stdout or "").strip():
        password = _replicator_password()
        env_file = _write_env_file({"PGPASSWORD": password})
        try:
            seed = _run(
                ["docker", "run", "-d", "--name", SHARED_STANDBY,
                 "--network", "smsly-net",
                 "--restart", "unless-stopped",
                 "--env-file", env_file,
                 "-v", f"{volume}:/var/lib/postgresql/data",
                 SHARED_IMAGE,
                "sh", "-c",
                f"until pg_isready -h {SHARED_CONTAINER} -p {SHARED_PORT} -q; "
                "do sleep 2; done; "
                # Fresh named volumes are root-owned 0755 and this seed
                # bypasses the image entrypoint (which would fix ownership).
                # Postgres refuses anything but 0700/0750 — chown alone
                # keeps 0755 and still fails (observed live, twice).
                "find /var/lib/postgresql/data -mindepth 1 -delete; "
                "chown postgres:postgres /var/lib/postgresql/data; "
                "chmod 0700 /var/lib/postgresql/data; "
                f"gosu postgres pg_basebackup -h {SHARED_CONTAINER} -p {SHARED_PORT} "
                f"-U {REPLICATOR_ROLE} -D /var/lib/postgresql/data -Fp -Xs -P -R; "
                # Same tuning flags as the primary: command-line settings
                # do not replicate, and recovery aborts when the standby's
                # max_connections is lower (observed live).
                "exec gosu postgres postgres " + " ".join(POSTGRES_TUNING_FLAGS)],
                timeout=600,
            )
            if seed.returncode != 0 and "already in use" not in (seed.stderr or ""):
                raise RuntimeError(
                    f"shared standby create failed: {(seed.stderr or '').strip()[:200]}")
        finally:
            try:
                os.remove(env_file)
            except OSError:
                pass
    if not _standby_running():
        _run(["docker", "start", SHARED_STANDBY], timeout=60)
    _wait_container_ready(SHARED_STANDBY)
    if not _primary_conninfo_ok(SHARED_CONTAINER):
        raise RuntimeError("shared standby did not reach streaming state")
    logger.info("Shared Postgres standby streaming")
    if reseed:
        _gc_old_standby_volumes(volume)
    return SHARED_STANDBY


def shared_standby_lag_seconds() -> float | None:
    """Replication lag of the shared standby in seconds.

    ``None`` when the standby is absent, unreachable, or not streaming
    (callers treat unknown as unhealthy, never as zero). A streaming
    standby with NULL replay_lag is idle, not broken — report 0.0
    (reporting None here kept every idle system DEGRADED forever).
    """
    try:
        out = _psql(
            "postgres",
            "SELECT state, coalesce(extract(epoch from replay_lag), 0) "
            "FROM pg_stat_replication "
            "ORDER BY replay_lag DESC NULLS LAST LIMIT 1;",
        )
        parts = (out.splitlines() or ["|"])[0].split("|")
        if len(parts) >= 2 and parts[0].strip() == "streaming":
            return max(0.0, float(parts[1].strip() or 0))
        return None
    except Exception:
        return None


def shared_ha_status() -> dict:
    """Machine-readable shared-HA state for views/health checks."""
    try:
        primary_up = _container_running()
        standby_up = _standby_running()
        lag = shared_standby_lag_seconds() if primary_up else None
    except Exception:
        return {"state": "UNKNOWN", "primary": None, "standby": None,
                "lag_seconds": None}
    if primary_up and standby_up and lag is not None:
        state = "HEALTHY"
    elif primary_up and standby_up:
        state = "DEGRADED"
    elif primary_up:
        state = "STANDALONE"
    else:
        state = "DOWN"
    return {
        "state": state,
        "primary": SHARED_CONTAINER if primary_up else None,
        "standby": SHARED_STANDBY if standby_up else None,
        "lag_seconds": lag,
    }


def _container_networks(container: str) -> dict[str, list[str]]:
    """Map network name -> aliases for a container (best effort)."""
    proc = _run(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container],
        timeout=30,
    )
    if proc.returncode != 0:
        return {}
    import json

    try:
        nets = json.loads((proc.stdout or "").strip() or "{}")
    except Exception:
        return {}
    return {
        name: list((info or {}).get("Aliases") or [])
        for name, info in (nets or {}).items()
    }


def promote_shared_standby(force: bool = False) -> str:
    """Fail over to the shared standby. Returns the new primary's name.

    Order (split-brain safe, mirrors per-addon HA): refuse while the
    primary answers unless ``force`` (DR drills) → fence (stop) the old
    primary → promote → verify writable → move every DNS alias to the
    promoted container so app URLs keep working → prepare replication
    access on the new primary for the next reseed.

    The fenced old primary is left stopped for the operator to re-seed
    (remove it and re-run ensure) — never deleted automatically.
    """
    if not _standby_running():
        raise RuntimeError("no shared standby running to promote")
    if not force:
        try:
            alive = _psql("postgres", "SELECT pg_is_in_recovery();", timeout=15)
            if (alive.splitlines() or [""])[0].strip().lower() == "f":
                raise RuntimeError(
                    "shared primary is alive; refusing promote without force "
                    "(would split-brain). Pass force=True for a DR drill.")
        except RuntimeError:
            raise
        except Exception:
            pass
    _run(["docker", "stop", "-t", "30", SHARED_CONTAINER], timeout=120)
    logger.info("shared postgres: fenced old primary %s", SHARED_CONTAINER)
    proc = _run(
        ["docker", "exec", SHARED_STANDBY,
         "gosu", "postgres", "pg_ctl", "promote", "-D", "/var/lib/postgresql/data",
         "-t", "60"],
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"shared standby promote failed: {(proc.stderr or '').strip()[:200]}")
    _wait_container_ready(SHARED_STANDBY)
    try:
        writable = _psql_on(SHARED_STANDBY, "postgres", "SELECT pg_is_in_recovery();", timeout=15)
        if (writable.splitlines() or [""])[0].strip().lower() != "f":
            raise RuntimeError("promoted standby still in recovery")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"promote verification failed: {exc}") from exc
    # Move every alias (tenant hostnames) to the promoted container.
    for network, aliases in _container_networks(SHARED_CONTAINER).items():
        if not aliases:
            continue
        _run(["docker", "network", "disconnect", network, SHARED_CONTAINER], timeout=60)
        cmd = ["docker", "network", "connect"]
        for entry in dict.fromkeys(aliases):
            cmd += ["--alias", entry]
        cmd += [network, SHARED_STANDBY]
        moved = _run(cmd, timeout=60)
        if moved.returncode != 0:
            logger.warning("alias move to promoted standby failed on %s", network)
    # Rename so SHARED_CONTAINER always names the live primary: every
    # future ensure/provision/reseed keeps working unchanged.
    import time as _time

    fenced = f"{SHARED_CONTAINER}-fenced-{int(_time.time())}"
    _run(["docker", "rename", SHARED_CONTAINER, fenced], timeout=60)
    _run(["docker", "rename", SHARED_STANDBY, SHARED_CONTAINER], timeout=60)
    logger.info("shared postgres: fenced %s, primary is now %s", fenced, SHARED_CONTAINER)
    _ensure_replication_access_on(SHARED_CONTAINER)
    logger.info("shared postgres: promoted %s", SHARED_CONTAINER)
    return SHARED_CONTAINER


def _psql_on(container: str, database: str, sql: str, timeout: int = 60) -> str:
    """Run SQL as superuser inside ``container``; return stdout."""
    password = _superuser_password()
    env_file = _write_env_file({"PGPASSWORD": password})
    try:
        proc = _run(
            ["docker", "exec", "--env-file", env_file, container,
             "psql", "-U", "postgres", "-d", database, "-tAc", sql],
            timeout=timeout,
        )
    finally:
        try:
            os.remove(env_file)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(f"shared postgres SQL failed: {(proc.stderr or '').strip()[:200]}")
    return (proc.stdout or "").strip()


def _ensure_replication_access_on(container: str) -> None:
    """Create the replicator role + pg_hba rule on ``container`` (idempotent)."""
    password = _replicator_password()
    _psql_on(
        container, "postgres",
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{REPLICATOR_ROLE}') THEN "
        f"CREATE ROLE {REPLICATOR_ROLE} WITH REPLICATION LOGIN PASSWORD '{password}'; "
        "END IF; END $$;",
    )
    proc = _run(
        ["docker", "exec", container, "sh", "-c",
         f"grep -q 'host replication {REPLICATOR_ROLE}' "
         "$PGDATA/pg_hba.conf || echo "
         f"'host replication {REPLICATOR_ROLE} 0.0.0.0/0 scram-sha-256' "
         ">> $PGDATA/pg_hba.conf"],
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError("shared postgres pg_hba update failed")
    _psql_on(container, "postgres", "SELECT pg_reload_conf();")


def _psql(database: str, sql: str, timeout: int = 60) -> str:
    """Run SQL as superuser inside the shared container; return stdout."""
    password = _superuser_password()
    env_file = _write_env_file({"PGPASSWORD": password})
    try:
        proc = _run(
            ["docker", "exec", "--env-file", env_file, SHARED_CONTAINER,
             "psql", "-U", "postgres", "-d", database, "-tAc", sql],
            timeout=timeout,
        )
    finally:
        try:
            os.remove(env_file)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(f"shared postgres SQL failed: {(proc.stderr or '').strip()[:200]}")
    return (proc.stdout or "").strip()


def _role_exists(db_user: str) -> bool:
    out = _psql("postgres", f"SELECT 1 FROM pg_roles WHERE rolname={_lit(db_user)};")
    return out.splitlines()[0].strip() == "1" if out else False


def _db_exists(db_name: str) -> bool:
    out = _psql("postgres", f"SELECT 1 FROM pg_database WHERE datname={_lit(db_name)};")
    return out.splitlines()[0].strip() == "1" if out else False


def _lit(value: str) -> str:
    """Quote an SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def ensure_logical_db(db_user: str, db_name: str, password: str) -> None:
    """Idempotently create role + database with tenant lockdown.

    * Password is always (re)set — retries converge instead of drifting.
    * New databases revoke CONNECT from PUBLIC (Postgres default grants
      it) and grant it only to the owner.
    * ``statement_timeout`` + ``CONNECTION LIMIT`` bound noisy neighbors.
    """
    user_q = _quote_ident(db_user)
    db_q = _quote_ident(db_name)
    if not _role_exists(db_user):
        _psql(
            "postgres",
            f"CREATE ROLE {user_q} WITH LOGIN PASSWORD {_lit(password)} "
            f"CONNECTION LIMIT {ROLE_CONNECTION_LIMIT};",
        )
    _psql(
        "postgres",
        f"ALTER ROLE {user_q} WITH PASSWORD {_lit(password)} "
        f"CONNECTION LIMIT {ROLE_CONNECTION_LIMIT};",
    )
    _psql(
        "postgres",
        f"ALTER ROLE {user_q} SET statement_timeout = {_lit(ROLE_STATEMENT_TIMEOUT)};",
    )
    if not _db_exists(db_name):
        _psql("postgres", f"CREATE DATABASE {db_q} OWNER {user_q};")
        _psql("postgres", f"REVOKE CONNECT ON DATABASE {db_q} FROM PUBLIC;")
        _psql("postgres", f"GRANT CONNECT ON DATABASE {db_q} TO {user_q};")
    # System databases grant CONNECT to PUBLIC by default — close that
    # for every tenant role on every ensure (idempotent). Otherwise a
    # tenant can open the postgres/template catalogs (2026-09-18
    # prove-out: tenant got `SELECT 1` from the postgres db).
    for sysdb in ("postgres", "template1"):
        try:
            _psql("postgres", f"REVOKE CONNECT ON DATABASE {_quote_ident(sysdb)} FROM {user_q};")
        except Exception:
            pass
    _psql(db_name, "CREATE EXTENSION IF NOT EXISTS vector;")


def drop_database_only(db_name: str) -> None:
    """Terminate backends and drop ONE database. Idempotent.

    Unlike :func:`drop_logical_db` this never touches roles — used when
    the owning role must survive (migration staging databases share the
    source's role).
    """
    if db_name:
        _psql(
            "postgres",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname={_lit(db_name)} AND pid <> pg_backend_pid();",
        )
        _psql("postgres", f"DROP DATABASE IF EXISTS {_quote_ident(db_name)};")


def drop_logical_db(db_user: str, db_name: str) -> None:
    """Terminate backends, drop database + role. Idempotent."""
    if db_name:
        _psql(
            "postgres",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname={_lit(db_name)} AND pid <> pg_backend_pid();",
        )
        _psql("postgres", f"DROP DATABASE IF EXISTS {_quote_ident(db_name)};")
    if db_user:
        _psql("postgres", f"DROP ROLE IF EXISTS {_quote_ident(db_user)};")


def database_size_bytes(db_name: str) -> int:
    """On-disk size of one logical database (quotas/monitoring)."""
    out = _psql(
        "postgres",
        f"SELECT pg_database_size({_lit(db_name)});",
    )
    try:
        return int((out.splitlines() or ["0"])[0].strip())
    except (TypeError, ValueError):
        return 0


def health() -> bool:
    """True when the shared server answers."""
    try:
        ensure_shared_server()
    except Exception:
        return False
    try:
        out = _psql("postgres", "SELECT 1;", timeout=15)
        return out.splitlines()[0].strip() == "1" if out else False
    except Exception:
        return False


def _endpoint_aliases(container: str, network: str) -> list[str]:
    proc = _run(
        ["docker", "inspect", "-f",
         "{{range $k, $v := .NetworkSettings.Networks}}"
         "{{if eq $k \"" + network + "\"}}{{json $v.Aliases}}{{end}}{{end}}",
         container],
        timeout=30,
    )
    if proc.returncode != 0:
        return []
    import json

    try:
        aliases = json.loads((proc.stdout or "").strip() or "[]")
        return list(aliases) if isinstance(aliases, list) else []
    except Exception:
        return []


def attach_alias(network: str, alias: str, container: str | None = None) -> None:
    """Join ``container`` (default: shared server) to ``network`` with DNS ``alias`` (idempotent).

    Preserves the app-facing URL shape (``postgres-myapp``) so services
    need no changes when moving container → logical.

    Subtlety: ``docker network connect`` refuses an already-attached
    container, so when the endpoint exists but lacks the alias we
    disconnect and reconnect carrying the FULL alias set (otherwise the
    new alias is silently dropped and DNS never resolves — observed
    live). Brief blip on that endpoint; callers run this at provision /
    spawn time, and clients retry.
    """
    target = container or SHARED_CONTAINER
    ensure_shared_server()
    current = _endpoint_aliases(target, network)
    if alias in current:
        return
    # Reconnect carrying the FULL alias set: `connect` refuses an
    # already-attached container, so a plain connect would silently drop
    # the new alias (DNS never resolves). The disconnect is a no-op when
    # absent, so one path covers both cases.
    wanted = list(dict.fromkeys([*current, alias]))
    _run(["docker", "network", "disconnect", network, target], timeout=60)
    cmd = ["docker", "network", "connect"]
    for entry in wanted:
        cmd += ["--alias", entry]
    cmd += [network, target]
    proc = _run(cmd, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"shared postgres attach to {network} failed: {(proc.stderr or '').strip()[:200]}")
