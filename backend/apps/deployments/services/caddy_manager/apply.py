import hashlib
import json
import logging
import os
import subprocess
import time

from .tls import (
    CADDY_CONFIG_DIR,
    CADDY_TOKEN_CACHE,
    CADDY_TOKEN_CACHE_TTL_SECONDS,
    CADDY_TOKEN_CLEAR_FILE,
    CADDY_TOKEN_FILE,
    _load_cached_token,
)
from .utils import caddy_disabled_mode
from .validation import validate_service_routes_do_not_hit_control_plane

logger = logging.getLogger(__name__)

CADDY_FILE_PATH = os.path.join(CADDY_CONFIG_DIR, "Caddyfile")
CADDY_RELOAD_FLAG = os.path.join(CADDY_CONFIG_DIR, ".reload")
CADDY_RELOAD_COOLDOWN_SECONDS = 10
_last_caddy_reload_ts: float = 0.0
_last_caddy_content_hash: str = ""


def apply_caddyfile(content: str, cloudflare_token: str = "", preserve_existing_token: bool = True) -> dict:
    global _last_caddy_reload_ts, _last_caddy_content_hash

    if caddy_disabled_mode():
        logger.debug("Caddy-disabled mode: skipping apply_caddyfile()")
        return {"ok": True, "message": "Skipped because Caddy is not part of this node"}

    now = time.time()
    elapsed = now - _last_caddy_reload_ts
    if elapsed < CADDY_RELOAD_COOLDOWN_SECONDS:
        content_hash = hashlib.md5(content.encode()).hexdigest()
        if content_hash == _last_caddy_content_hash:
            logger.info(
                "Skipping Caddy reload — identical content, %.1fs since last reload (cooldown=%ds)",
                elapsed, CADDY_RELOAD_COOLDOWN_SECONDS,
            )
            return {"ok": True, "message": f"Skipped (cooldown, identical content, {elapsed:.1f}s since last reload)"}
        logger.info(
            "Caddy content changed within cooldown window — proceeding with reload (%.1fs since last)",
            elapsed,
        )

    result = {"ok": False, "message": ""}

    cloudflare_token = (cloudflare_token or "").strip()
    if not cloudflare_token and preserve_existing_token:
        cloudflare_token = _load_cached_token()

    try:
        route_errors = validate_service_routes_do_not_hit_control_plane(content)
        if route_errors:
            result["message"] = (
                "Refusing to apply Caddyfile because service routes would hit "
                f"the control plane: {'; '.join(route_errors[:5])}"
            )
            logger.error(result["message"])
            return result

        os.makedirs(CADDY_CONFIG_DIR, exist_ok=True)
        try:
            os.chmod(CADDY_CONFIG_DIR, 0o775)
        except (OSError, PermissionError) as chmod_exc:
            logger.warning("chmod on %s failed (%s) — continuing with probe", CADDY_CONFIG_DIR, chmod_exc)
        probe = os.path.join(CADDY_CONFIG_DIR, ".perm_probe")
        probe_ok = False
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            probe_ok = True
        except (OSError, PermissionError) as probe_exc:
            try:
                CADDY_GID = int(os.environ.get("CADDY_GID", "1000"))
                if hasattr(os, "chown"):
                    os.chown(CADDY_CONFIG_DIR, os.getuid(), CADDY_GID)
                else:
                    logger.warning(
                        "os.chown unavailable on this platform; skipping ownership "
                        "change for %s (owner=%s gid=%s)",
                        CADDY_CONFIG_DIR, os.getuid(), CADDY_GID,
                    )
                os.chmod(CADDY_CONFIG_DIR, 0o775)
                logger.warning(
                    "Self-healed caddy-config dir ownership to uid=%s gid=%s "
                    "(previous probe failed: %s)",
                    os.getuid(), CADDY_GID, probe_exc,
                )
                probe_ok = True
            except (OSError, PermissionError, ValueError) as chown_exc:
                logger.warning(
                    "Cannot chmod/chown %s (probe_exc=%s chown_exc=%s) — "
                    "continuing anyway; if Caddyfile write fails, fix host perms",
                    CADDY_CONFIG_DIR, probe_exc, chown_exc,
                )
                probe_ok = True  # chmod/chown failing is non-fatal

        if not probe_ok:
            raise PermissionError(
                f"Cannot write to {CADDY_CONFIG_DIR}. "
                "Fix host permissions: sudo chown -R 0:1000 "
                f"{CADDY_CONFIG_DIR} && sudo chmod 775 {CADDY_CONFIG_DIR}."
            )

        tmp_path = os.path.join(CADDY_CONFIG_DIR, ".Caddyfile.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, CADDY_FILE_PATH)
        try:
            os.chmod(CADDY_FILE_PATH, 0o664)
        except (OSError, PermissionError) as chmod_exc:
            logger.warning("chmod on %s failed (%s) — continuing", CADDY_FILE_PATH, chmod_exc)

        if cloudflare_token:
            try:
                os.chmod(CADDY_CONFIG_DIR, 0o775)
            except (OSError, PermissionError):
                pass
            with os.fdopen(os.open(CADDY_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write(cloudflare_token)
            cache_payload = json.dumps({
                "token": cloudflare_token,
                "expires_at": time.time() + CADDY_TOKEN_CACHE_TTL_SECONDS,
            })
            with os.fdopen(os.open(CADDY_TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write(cache_payload)
            if os.path.exists(CADDY_TOKEN_CLEAR_FILE):
                os.remove(CADDY_TOKEN_CLEAR_FILE)
        else:
            if os.path.exists(CADDY_TOKEN_FILE):
                os.remove(CADDY_TOKEN_FILE)
            if os.path.exists(CADDY_TOKEN_CACHE):
                os.remove(CADDY_TOKEN_CACHE)
            with os.fdopen(os.open(CADDY_TOKEN_CLEAR_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write("clear")

        try:
            with open(CADDY_RELOAD_FLAG, "w", encoding="utf-8") as f:
                f.write(str(int(__import__("time").time())))
            os.chmod(CADDY_RELOAD_FLAG, 0o664)
            logger.info("Wrote .reload flag to %s", CADDY_RELOAD_FLAG)
        except Exception as flag_exc:
            logger.warning("Failed to write .reload flag: %s", flag_exc)

        _last_caddy_reload_ts = time.time()
        _last_caddy_content_hash = hashlib.md5(content.encode()).hexdigest()

        CONTAINER_NAME = "smsly-hosting-caddy-1"
        logger.info("Attempting fast-path Caddy reload via Docker exec %s...", CONTAINER_NAME)
        try:
            dock_res = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if dock_res.returncode == 0:
                result["ok"] = True
                result["message"] = "Caddyfile written and reloaded via Docker"
                logger.info("Caddy reloaded via docker exec on %s", CONTAINER_NAME)
            else:
                logger.info(
                    "Docker exec reload not available (%s) — host-side watcher will handle it",
                    dock_res.stderr.strip()[:200],
                )
                result["ok"] = True
                result["message"] = "Caddyfile written; reload pending via host-side watcher"
        except FileNotFoundError:
            logger.info("Docker CLI not found in container — host-side watcher will handle reload")
            result["ok"] = True
            result["message"] = "Caddyfile written; reload pending via host-side watcher"
        except subprocess.TimeoutExpired:
            logger.info("Docker exec timed out — host-side watcher will handle reload")
            result["ok"] = True
            result["message"] = "Caddyfile written; reload pending via host-side watcher"
        except Exception as exec_exc:
            logger.info("Docker exec failed (%s) — host-side watcher will handle reload", exec_exc)
            result["ok"] = True
            result["message"] = "Caddyfile written; reload pending via host-side watcher"

    except Exception as exc:
        result["message"] = f"Failed to apply Caddyfile: {exc}"
        if isinstance(exc, PermissionError):
            result["message"] = str(result["message"]) + (
                " | Fix host dir perms: sudo chown -R 0:1000 /opt/smsly-hosting/caddy-config "
                "&& sudo chmod 775 /opt/smsly-hosting/caddy-config"
            )
        logger.error("Failed to write Caddyfile: %s", exc)

    return result
