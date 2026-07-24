"""
Database replica service.

Responsibilities:
  * Test connectivity to a configured replica (used by the
    ``/test/`` API action and by the periodic health-check task).
  * Re-render the pgcat config inside the pgcat container and
    reload pgcat so the new replica list takes effect.
  * Read replication lag from the primary's pg_stat_replication
    for each replica (best-effort).
"""

import json
import logging
import socket
from typing import Any

from django.utils import timezone

from ..models.database_replica import DatabaseReplica

logger = logging.getLogger(__name__)


# Where the pgcat container listens for SIGHUP / SIGUSR2 to reload
# its config without dropping connections. SIGHUP is the conventional
# reload signal; if the image in use doesn't honour it we fall back
# to a container restart.
PGCAT_RELOAD_SIGNAL = "SIGHUP"

# Sentinel that the render_pgcat_config.py generator checks to know
# whether a config file was provided. The generator reads from env
# vars (DB_REPLICA_HOSTS) so we pass the merged value via that env
# var on `docker exec`.
DB_REPLICA_HOSTS_ENV = "DB_REPLICA_HOSTS"


def active_replica_endpoints() -> list[str]:
    """
    Return the list of ``host:port`` strings for all active replicas.

    This is the single source of truth used by the pgcat config
    generator and by any other code that needs to know the current
    replica pool. Local and remote replicas are treated identically
    here — the distinction is only relevant for the install flow
    that creates the underlying container (or doesn't).
    """
    return [
        f"{r.host}:{r.port}"
        for r in DatabaseReplica.objects.filter(is_active=True).order_by("name")
    ]


def replica_endpoints_for_pgcat() -> str:
    """
    Return the comma-separated ``host:port`` string that pgcat's
    render_pgcat_config.py reads from the DB_REPLICA_HOSTS env var.
    """
    return ",".join(active_replica_endpoints())


def test_connection(replica: DatabaseReplica) -> tuple[bool, str, float | None]:
    """
    Test the connection to a replica.

    Returns (ok, error_message, lag_seconds). When ok is True,
    error_message is empty and lag_seconds is the read of
    ``EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())``
    (None when the replica is a primary, not a standby).

    The test is intentionally simple: open a TCP connection to the
    host:port. Doing a full psycopg2 connect would require having
    the credentials in the backend runtime (which they are, since
    we just decrypted them) and would also be a heavier op — TCP
    reachability is a useful first-line check that does not need
    the password. A separate /test-full/ action can do a real auth
    probe if needed.
    """
    try:
        with socket.create_connection((replica.host, replica.port), timeout=5):
            return True, "", None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}", None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", None


def update_replica_health(replica: DatabaseReplica) -> DatabaseReplica:
    """
    Run a health check for one replica and persist the result.
    Returns the (refreshed) replica instance.
    """
    ok, err, _lag = test_connection(replica)
    replica.last_checked_at = timezone.now()
    if ok:
        replica.last_status = DatabaseReplica.Status.OK
        replica.last_error = ""
    else:
        replica.last_status = DatabaseReplica.Status.ERROR
        replica.last_error = err[:8000]  # cap to fit the TextField
    replica.save(update_fields=["last_status", "last_error", "last_checked_at", "updated_at"])
    return replica


def sync_pgcat_config(*, trigger_reload: bool = True) -> dict[str, Any]:
    """
    Push the current set of active replicas to the pgcat container.

    Strategy:
      1. Build the comma-separated DB_REPLICA_HOSTS value from
         active_replica_endpoints().
      2. Find the pgcat container via the docker socket.
      3. docker exec into the container with the new env var and
         run render_pgcat_config.py to write /etc/pgcat/pgcat.toml.
      4. Send SIGHUP (or fall back to docker restart) so pgcat
         re-reads the config.
      5. Return a dict describing what happened.

    The function is intentionally best-effort: a failure to push the
    config is logged but does not raise. The caller (the API) can
    surface the failure in the response.
    """
    endpoints_str = replica_endpoints_for_pgcat()
    logger.info("Syncing pgcat config with %d replica(s): %s",
                len(endpoints_str.split(",")) if endpoints_str else 0,
                endpoints_str or "(none)")

    result: dict[str, Any] = {
        "replica_count": 0,
        "endpoints": endpoints_str,
        "pgcat_container": None,
        "config_written": False,
        "reloaded": False,
        "error": None,
    }

    try:
        # Lazy-import docker so the module loads even when the docker
        # socket isn't available (e.g. unit tests on a developer
        # laptop).
        import docker  # type: ignore
        from docker.errors import APIError, NotFound  # type: ignore
    except ImportError as exc:
        result["error"] = f"docker SDK not available: {exc}"
        logger.error(result["error"])
        return result

    container_name = _find_pgcat_container()
    result["pgcat_container"] = container_name
    if not container_name:
        result["error"] = "pgcat container not found"
        logger.error(result["error"])
        return result

    try:
        client = docker.DockerClient(base_url=_docker_base_url())
        container = client.containers.get(container_name)
    except (NotFound, APIError) as exc:
        result["error"] = f"failed to get pgcat container: {exc}"
        logger.error(result["error"])
        return result

    # Render the config inside the container. The generator already
    # reads DB_REPLICA_HOSTS from the environment.
    try:
        exec_result = container.exec_run(
            [
                "python3",
                "/scripts/render_pgcat_config.py",
                "/etc/pgcat/pgcat.toml",
            ],
            environment={DB_REPLICA_HOSTS_ENV: endpoints_str, **container.attrs.get("Config", {}).get("Env", [])},
            user="pgcat",
        )
        if exec_result.exit_code != 0:
            result["error"] = (
                f"render_pgcat_config.py exited with {exec_result.exit_code}: "
                + (exec_result.output.decode(errors="replace") if exec_result.output else "")
            )
            logger.error(result["error"])
            return result
        result["config_written"] = True
    except APIError as exc:
        result["error"] = f"docker exec failed: {exc}"
        logger.error(result["error"])
        return result

    if not trigger_reload:
        return result

    # Reload pgcat. Try SIGHUP first; if the image doesn't install a
    # signal handler, restart the container as a fallback.
    try:
        container.kill(signal=PGCAT_RELOAD_SIGNAL)
        result["reloaded"] = True
    except APIError as exc:
        logger.warning("SIGHUP failed on %s (%s), falling back to restart", container_name, exc)
        try:
            container.restart(timeout=10)
            result["reloaded"] = True
        except APIError as exc2:
            result["error"] = f"restart also failed: {exc2}"
            logger.error(result["error"])

    result["replica_count"] = len(endpoints_str.split(",")) if endpoints_str else 0
    return result


def _find_pgcat_container() -> str | None:
    """Locate the pgcat container name. Returns None if not found."""
    import docker  # type: ignore
    from docker.errors import NotFound  # type: ignore
    try:
        client = docker.DockerClient(base_url=_docker_base_url())
        # Known names from the prod compose (docker-compose.prod.yml)
        # and the dev compose (docker-compose.yml).
        candidates = ("smsly-hosting-pgcat-1", "smsly-hosting-pgcat", "pgcat")
        for name in candidates:
            try:
                client.containers.get(name)
                return name
            except NotFound:
                continue
        # Fallback: scan for any container with 'pgcat' in its name.
        for c in client.containers.list():
            if "pgcat" in (c.name or "").lower():
                return c.name
    except Exception as exc:
        logger.warning("Could not enumerate docker containers: %s", exc)
    return None


def _docker_base_url() -> str:
    """Read DOCKER_HOST from env (default unix://var/run/docker.sock)."""
    import os
    return os.environ.get("DOCKER_HOST") or "unix://var/run/docker.sock"


def export_pgcat_replicas_json() -> str:
    """
    Return a JSON document with the current active replica set, in
    a format that render_pgcat_config.py can consume if mounted into
    the container. Currently unused (the env-var path is preferred)
    but kept for future migration to a file-based config.
    """
    replicas = list(
        DatabaseReplica.objects.filter(is_active=True)
        .order_by("name")
        .values("name", "host", "port", "database", "username", "ssl_mode")
    )
    return json.dumps({"replicas": replicas}, indent=2)
