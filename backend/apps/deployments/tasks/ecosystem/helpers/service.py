import logging
import secrets
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.deployments.tasks.ecosystem.constants import (
    _STACK_DEFAULT_PORTS,
    _VALID_PORT_RANGE,
)

from .repo import (
    _repository_url,
    _slugify_name,
)

logger = logging.getLogger(__name__)


def _deployment_target_for_server(server, provider) -> tuple[Any, bool]:
    """Translate a selected server into Deployment target fields."""
    if server is None:
        return None, str(getattr(provider, "provider_type", "")).upper() == "LOCAL"
    if bool(getattr(server, "is_primary", False)):
        return None, True
    return server, False


def _next_available_service_name(ServiceModel, base_name: str) -> str:
    """Find a globally unique service name."""
    if not ServiceModel.objects.filter(name=base_name).exists():
        return base_name

    for _ in range(20):
        candidate = f"{base_name}-{secrets.token_hex(2)}"
        if not ServiceModel.objects.filter(name=candidate).exists():
            return candidate

    return f"{base_name}-{secrets.token_hex(4)}"


def _detect_service_port(svc_plan: dict, stack: str = "") -> int:
    """Detect the internal port for a service from multiple sources.

    Priority:
      1. Explicit port in svc_plan (user-provided)
      2. Dockerfile EXPOSE directive (cloned repo)
      3. docker-compose.yml ports mapping
      4. .env / .env.example PORT variable
      5. Stack-aware default (e.g. Django=8000, Next.js=3000)
      6. Global fallback (3000)
    """
    # 1. Explicit port in plan
    explicit = svc_plan.get("port")
    if explicit is not None:
        try:
            p = int(explicit)
            if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                return p
        except (TypeError, ValueError):
            pass

    _clone_dir = svc_plan.get("_clone_dir", "")
    if not _clone_dir:
        # No cloned source — skip file-based detection
        stack_l = str(stack or "").strip().lower()
        return _STACK_DEFAULT_PORTS.get(stack_l, 3000)

    import os

    # 2. Dockerfile EXPOSE
    dockerfile_path = svc_plan.get("dockerfile", "")
    try:
        df_path = os.path.join(_clone_dir, dockerfile_path or "Dockerfile")
        if os.path.isfile(df_path):
            with open(df_path, "r", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.upper().startswith("EXPOSE"):
                        parts = stripped.split()
                        if len(parts) >= 2:
                            for part in parts[1:]:
                                port_str = part.split("/")[0]
                                try:
                                    p = int(port_str)
                                    if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                                        return p
                                except (TypeError, ValueError):
                                    continue
    except Exception as exc:
        logger.debug("Failed to detect port from Dockerfile: %s", exc)

    # 3. docker-compose.yml ports mapping (first "host:container" → container port)
    for dc_name in ("docker-compose.yml", "docker-compose.yaml"):
        try:
            dc_path = os.path.join(_clone_dir, dc_name)
            if os.path.isfile(dc_path):
                with open(dc_path, "r", errors="ignore") as f:
                    in_ports = False
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith("ports:"):
                            in_ports = True
                            continue
                        if in_ports:
                            if stripped.startswith("- "):
                                # e.g. "- 8080:3000" or "- "3000:3000""
                                port_part = stripped.lstrip("- ").strip().strip('"').strip("'")
                                container_port = port_part.split(":")[-1].split("/")[0]
                                try:
                                    p = int(container_port)
                                    if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                                        return p
                                except (TypeError, ValueError):
                                    continue
                            else:
                                in_ports = False
        except Exception as exc:
            logger.debug("Failed to detect port from docker-compose: %s", exc)

    # 4. .env / .env.example PORT variable
    for env_name in (".env.example", ".env", ".env.local"):
        try:
            env_path = os.path.join(_clone_dir, env_name)
            if os.path.isfile(env_path):
                with open(env_path, "r", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.upper().startswith("PORT="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            try:
                                p = int(val)
                                if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                                    return p
                            except (TypeError, ValueError):
                                continue
        except Exception as exc:
            logger.debug("Failed to detect port from .env file: %s", exc)

    # 5. Stack-aware default
    stack_l = str(stack or "").strip().lower()
    return _STACK_DEFAULT_PORTS.get(stack_l, 3000)


def _stack_runtime_defaults(stack: str, port: int) -> dict[str, str]:
    """Inject safe runtime defaults per stack."""
    stack_l = str(stack or "").strip().lower()
    defaults: dict[str, str] = {"PORT": str(max(1, int(port or 3000)))}

    if stack_l in {"node", "nextjs", "nuxt"}:
        defaults["NODE_ENV"] = "production"
    if stack_l in {"python", "django"}:
        defaults["PYTHONUNBUFFERED"] = "1"
        defaults["PYTHONDONTWRITEBYTECODE"] = "1"

    return defaults


def _apply_service_profile(service, svc_plan: dict[str, Any], provider, port: int, server=None):
    """Apply ecosystem plan profile to a service with production defaults.

    Important: user-customisable fields (health_check_path, internal_port,
    cpu_cores, memory_mb) are only set from the plan when they still hold
    their model defaults.  This prevents the ecosystem deploy from silently
    overriding values the user changed through the web UI.
    """
    buildpack = _normalize_buildpack(svc_plan.get("build"))
    deploy_mode, compose_file, compose_main = _normalize_deploy_mode(svc_plan)
    root_directory = str(svc_plan.get("root_directory") or service.root_directory or "/").strip()
    if not root_directory.startswith("/"):
        root_directory = f"/{root_directory.lstrip('/')}"
    root_directory = root_directory or "/"

    service.repository_url = _repository_url(svc_plan["repo"])
    resolved_branch = str(
        svc_plan.get("branch")
        or svc_plan.get("default_branch")
        or service.branch
        or "main"
    ).strip() or "main"
    service.branch = resolved_branch

    # Only set port from plan if still at model default (8000) or unset/invalid.
    if not service.internal_port or service.internal_port == 8000:
        service.internal_port = int(port)

    service.buildpack = buildpack
    service.deploy_mode = deploy_mode
    service.compose_file = compose_file if deploy_mode == "COMPOSE" else ""
    service.compose_main_service = compose_main if deploy_mode == "COMPOSE" else ""
    service.root_directory = root_directory
    if not service.provider:
        service.provider = provider
    if server is not None or svc_plan.get("force_local_target"):
        service.server = server

    # Only set health_check_path from plan if user hasn't customised it
    # (still at model default "/health" or empty).
    health_path = str(svc_plan.get("health_check_path") or "").strip()
    if health_path:
        current_path = (service.health_check_path or "").strip()
        if not current_path or current_path == "/health":
            service.health_check_path = health_path if health_path.startswith("/") else f"/{health_path}"

    server_id = svc_plan.get("server_id")
    if server_id:
        from apps.deployments.models import ManagedServer
        try:
            if str(server_id).lower() in ("local", "primary"):
                server = ManagedServer.get_primary()
            else:
                server = ManagedServer.objects.filter(id=server_id, owner=service.owner).first()

            if server:
                service.server = server
        except Exception as exc:
            logger.debug("Failed to resolve deployment server: %s", exc)

    # Only set cpu_cores/memory_mb from plan if still at model defaults.
    cpu_cores = getattr(service, "cpu_cores", None)
    if not cpu_cores or float(cpu_cores) == 1.0:
        cpu = svc_plan.get("cpu_cores")
        if cpu:
            try:
                service.cpu_cores = Decimal(str(cpu))
            except Exception as exc:
                logger.debug("Failed to set cpu_cores from plan: %s", exc)
    memory_mb = getattr(service, "memory_mb", None)
    if not memory_mb or memory_mb == 2048:
        mem = svc_plan.get("memory_mb")
        if mem:
            try:
                service.memory_mb = int(mem)
            except Exception as exc:
                logger.debug("Failed to set memory_mb from plan: %s", exc)

    service.save()


def _update_plan_progress(plan_id: str, msg: str) -> None:
    """Persist scan progress so the frontend can show it on resume after page navigation."""
    from apps.deployments.models.ecosystem import EcosystemPlan
    try:
        EcosystemPlan.objects.filter(id=plan_id).update(
            scan_progress=msg,
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.debug("Failed to update ecosystem plan progress: %s", exc)


def _ecosystem_project_name(raw_name: str) -> str:
    """Generate a beautified, unique project name for ecosystem deployments.

    Produces names like: ``my-app — Jul 8, 2026 · 14:32``
    The timestamp ensures uniqueness across repeated deploys of the same ecosystem.
    """
    import calendar

    from django.utils import timezone as _tz

    base = _slugify_name(raw_name).replace("-", " ").strip()
    if not base:
        base = "Ecosystem"

    # Title-case the base name for readability
    base = " ".join(word.capitalize() for word in base.split())

    now = _tz.now()
    month_abbr = calendar.month_abbr[now.month]   # e.g. "Jul"
    day = now.day
    year = now.year
    hour = now.hour
    minute = now.minute
    return f"{base} — {month_abbr} {day}, {year} · {hour:02d}:{minute:02d}"


def _runtime_watch_defaults(user) -> dict[str, str]:
    """Default zero-click runtime monitoring configuration."""
    defaults = {
        "JULES_RUNTIME_WATCH": "true",
        "JULES_NOTIFY_IN_APP": "true",
        "JULES_NOTIFY_SMS": "true",
        "JULES_NOTIFY_EMAIL": "true",
        "JULES_NOTIFY_TELEGRAM": "false",
        "JULES_NOTIFY_WHATSAPP": "false",
    }
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        defaults["ALERT_EMAIL"] = email
    return defaults


def _order_key(item: Any) -> int:
    """Sort helper for deploy order."""
    if not isinstance(item, dict):
        return 99
    try:
        return int(item.get("deploy_order", 99))
    except (TypeError, ValueError):
        return 99


def _normalize_buildpack(raw: Any) -> str:
    """Map ecosystem plan build strategy to Service.buildpack choices.

    Default is DOCKER for ecosystem services.  Only falls back to NIXPACKS
    when the plan explicitly requests it or the build type is static.
    """
    build = str(raw or "").strip().lower()
    if build in {"docker", "dockerfile", "docker-file"}:
        return "DOCKER"
    if build in {"static", "static-site", "static_site"}:
        return "STATIC"
    if build in {"nixpacks"}:
        return "NIXPACKS"
    # Default: Docker build for ecosystem services
    return "DOCKER"


def _normalize_deploy_mode(svc_plan: dict[str, Any]) -> tuple[str, str, str]:
    """
    Resolve deploy mode and compose hints from plan.

    Returns: (deploy_mode, compose_file, compose_main_service)
    """
    mode_raw = str(svc_plan.get("deploy_mode") or "").strip().upper()
    build_raw = str(svc_plan.get("build") or "").strip().lower()
    compose_file = str(
        svc_plan.get("compose_file")
        or svc_plan.get("docker_compose_file")
        or ""
    ).strip()
    compose_main = str(
        svc_plan.get("compose_main_service")
        or svc_plan.get("main_service")
        or ""
    ).strip()

    if mode_raw == "COMPOSE" or build_raw in {"docker-compose", "compose"} or compose_file:
        return "COMPOSE", (compose_file or "docker-compose.yml"), compose_main
    return "SINGLE", "", ""
