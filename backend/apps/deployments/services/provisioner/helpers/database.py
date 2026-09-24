import logging
import os
import secrets
import time

from apps.deployments.models.servers import ManagedServer

from .logging import _append_log
from .server_config import server_install_mode

logger = logging.getLogger(__name__)


def node_db_user_preexists(username: str) -> bool:
    """True when the node DB role already exists. Fail-closed toward
    preservation: any check error returns True so rollback never drops a
    credential set it did not create (a failed re-provision must not take
    down the currently-online node)."""
    try:
        import psycopg2

        master_db_url = os.environ.get("DATABASE_URL")
        if not master_db_url:
            return True
        conn = psycopg2.connect(master_db_url, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (username,))
                return bool(cur.fetchone())
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("DB pre-existence check failed, assuming preexists: %s", exc)
        return True


def _provision_node_db_credentials(server: ManagedServer):
    from urllib.parse import urlparse

    master_db_url = os.environ.get("DATABASE_URL")
    if not master_db_url:
        return None, None

    node_id_short = str(server.id).split('-')[0]
    username = f"node_agent_{node_id_short}"

    metadata = server.provider_metadata or {}
    existing_pass = metadata.get("node_db_password") or server.node_db_password

    password = existing_pass or secrets.token_urlsafe(24)

    try:
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        conn = psycopg2.connect(master_db_url, connect_timeout=10)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        is_new_user = False
        created_now = False
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (username,))
            user_exists = bool(cur.fetchone())

            if not user_exists:
                cur.execute(sql.SQL("CREATE USER {} WITH PASSWORD %s").format(sql.Identifier(username)), (password,))
                is_new_user = True
                created_now = True
            elif not existing_pass:
                cur.execute(sql.SQL("ALTER USER {} WITH PASSWORD %s").format(sql.Identifier(username)), (password,))
                is_new_user = True

            parsed = urlparse(master_db_url)
            db_name = parsed.path.lstrip('/')

            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(username)
            ))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(username)))
            # NOTE: deliberately NO ALTER DEFAULT PRIVILEGES. Auto-granting
            # every future table (including auth/token tables created later)
            # silently widens a compromised node's blast radius over time.
            # If a future table needs agent access, re-provision credentials
            # (or grant explicitly) instead of pre-authorizing the unknown.

        logger.info("Provisioned Master DB credentials for node %s: %s (new_user=%s)", server.name, username, is_new_user)
        if not isinstance(server.provider_metadata, dict):
            server.provider_metadata = {}
        server.provider_metadata["node_db_user"] = username
        if created_now:
            # Marker: THIS run created the role and has not confirmed
            # success yet. The stale sweeper drops the role only when
            # this marker is present — a dead re-provision of a live
            # node (role preexisted, no marker) must never DROP the
            # role the currently-online node is using. Cleared on
            # success (provision_server DONE path) and on rollback drop.
            # Deliberately NOT set on the rotation branch below
            # (ALTER of a preexisting role): the role predates this run.
            server.provider_metadata["node_db_user_pending"] = True
        server.node_db_password = password
        server.provider_metadata.pop("node_db_password", None)
        server.save(update_fields=["provider_metadata", "node_db_password"])

        _rerender_pgcat_config()

        return username, password
    except Exception as e:
        logger.error("Failed to create node DB credentials for %s: %s", server.name, e)
        return None, None


def _restart_pgcat():
    def _find_pgcat_container():
        try:
            import docker as docker_lib
            client = docker_lib.from_env()
            for name in ("smsly-hosting-pgcat-1", "smsly-hosting-pgcat"):
                try:
                    return client.containers.get(name), name
                except docker_lib.errors.NotFound:
                    continue
        except Exception as exc:
            logger.warning("PgCat docker client init failed: %s", exc)
        return None, None

    def _wait_healthy(container, name, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                container.reload()
                health = container.attrs.get("State", {}).get("Health", {}).get("Status")
                if health == "healthy":
                    logger.info("PgCat %s is healthy.", name)
                    return True
            except Exception as exc:
                logger.debug("Failed to check PgCat container health: %s", exc)
            time.sleep(2)
        return False

    container, pgcat_name = _find_pgcat_container()
    if not container:
        logger.warning(
            "PgCat container not found — node agent pools will not be "
            "active until the next PgCat restart."
        )
        return

    try:
        exit_code, output = container.exec_run(
            ["python3", "/app/render_pgcat_config.py", "/tmp/pgcat.toml"],
            demux=True,
        )
        if exit_code == 0:
            container.exec_run(
                ["sh", "-c", "cp /tmp/pgcat.toml /etc/pgcat/pgcat.toml && kill -HUP 1"],
            )
            time.sleep(2)
            logger.info("PgCat hot-reloaded config via docker exec + SIGHUP.")
            return
        logger.warning("PgCat render exited %d: %s", exit_code, (output or b"").decode(errors="replace"))
    except Exception as exc:
        logger.info("PgCat hot-reload failed (%s), falling back to restart.", exc)

    for attempt in range(3):
        try:
            container.restart(timeout=10)
            logger.info("Restarted PgCat container %s (attempt %d/3).", pgcat_name, attempt + 1)
            if _wait_healthy(container, pgcat_name, timeout=20):
                return
            logger.warning("PgCat %s not healthy after restart attempt %d.", pgcat_name, attempt + 1)
        except Exception as exc:
            logger.warning("PgCat restart attempt %d failed: %s", attempt + 1, exc)
        if attempt < 2:
            time.sleep(5 * (attempt + 1))

    logger.warning(
        "PgCat %s did not become healthy after 3 restart attempts. "
        "Node agent pools may not be active.",
        pgcat_name,
    )


def _rerender_pgcat_config():
    try:
        import docker as docker_lib
        client = docker_lib.from_env()
        container = None
        for name in ("smsly-hosting-pgcat-1", "smsly-hosting-pgcat"):
            try:
                container = client.containers.get(name)
                break
            except docker_lib.errors.NotFound:
                continue
        if not container:
            logger.warning("PgCat container not found for config re-render.")
            return
        exit_code, output = container.exec_run(
            ["python3", "/app/render_pgcat_config.py", "/tmp/pgcat.toml"],
            demux=True,
        )
        if exit_code == 0:
            container.exec_run(
                ["sh", "-c", "cp /tmp/pgcat.toml /etc/pgcat/pgcat.toml && kill -HUP 1"],
            )
            time.sleep(1)
            logger.info("PgCat config re-rendered and reloaded via SIGHUP.")
        else:
            logger.warning(
                "PgCat render failed (exit %d): %s. "
                "NOT restarting from the provision path — a container restart "
                "drops every DB connection platform-wide. Re-run the render "
                "manually or restart PgCat explicitly during maintenance.",
                exit_code, (output or b"").decode(errors="replace"),
            )
    except Exception as exc:
        logger.warning(
            "PgCat re-render failed (%s). NOT restarting from the provision "
            "path — restart PgCat explicitly during maintenance if pools "
            "are stale.", exc,
        )


def _verify_agent_db_connectivity(ssh, server: ManagedServer, start_time: float):
    if server_install_mode(server) != "agent-lite":
        return

    _append_log(server, "Verifying agent DB connectivity via health endpoint...")
    # Fresh 120s window from NOW (not from provision start — the installer
    # alone takes 5-15 minutes, so an inherited deadline is already dead).
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            _stdin, stdout, _stderr = ssh.exec_command(
                "curl -sf --max-time 5 http://localhost:8000/health/ 2>/dev/null",
                timeout=10,
            )
            body = stdout.read().decode("utf-8", errors="replace").strip()
            if '"status":"healthy"' in body or '"database":"healthy"' in body:
                _append_log(server, "Agent DB connectivity verified (health endpoint reports healthy).")
                return
        except Exception as exc:
            # Best-effort probe: SSH failures must not fail provisioning.
            logger.debug("Agent health check failed: %s", exc)
        time.sleep(5)

    _append_log(
        server,
        "Agent health endpoint did not report healthy within the wait window. "
        "The node will be marked ONLINE but may require a manual restart if "
        "the database connection does not recover on its own.",
    )
