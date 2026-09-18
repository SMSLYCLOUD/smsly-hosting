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
ROLE_CONNECTION_LIMIT = 10
ROLE_STATEMENT_TIMEOUT = "30s"


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
                 "-c", f"max_connections={SHARED_MAX_CONNECTIONS}",
                 "-c", f"superuser_reserved_connections={SHARED_RESERVED_CONNECTIONS}"],
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
    return cid


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
    _psql(db_name, "CREATE EXTENSION IF NOT EXISTS vector;")


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


def attach_alias(network: str, alias: str) -> None:
    """Join the shared server to ``network`` with DNS ``alias`` (idempotent).

    Preserves the app-facing URL shape (``postgres-myapp``) so services
    need no changes when moving container → logical.
    """
    ensure_shared_server()
    if alias in _endpoint_aliases(SHARED_CONTAINER, network):
        return
    proc = _run(
        ["docker", "network", "connect", "--alias", alias, network, SHARED_CONTAINER],
        timeout=60,
    )
    if proc.returncode != 0 and "already exists" not in (proc.stderr or ""):
        raise RuntimeError(
            f"shared postgres attach to {network} failed: {(proc.stderr or '').strip()[:200]}")
