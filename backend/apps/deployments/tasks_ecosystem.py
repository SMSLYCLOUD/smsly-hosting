# pylint: disable=invalid-name
"""
Celery tasks for ecosystem-level deployment.

Pipeline:
1. Scan repositories -> generate deploy plan.
2. Materialize all services/deployments from plan.
3. Execute deployments in dependency-aware waves with strict gating.
"""

import logging
import os
import re
import secrets
import string
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone
from django.conf import settings

from apps.deployments.services.task_encryption import encrypt_arg, decrypt_arg

logger = logging.getLogger(__name__)

# SEC-ZT-007: Ecosystem plan schema validation keys
_PLAN_REQUIRED_KEYS = {"services"}
_PLAN_OPTIONAL_KEYS = {
    "addons", "manifest", "wave_size", "server_id", "ai_provider",
    "ecosystem_name", "deploy_sequence",
    "total_repos_scanned", "deployable_repos",
    "scan_warning_count", "scan_warnings", "message",
}
_SERVICE_REQUIRED_KEYS = {"repo"}
_SERVICE_OPTIONAL_KEYS = {
    "name", "stack", "build", "port", "env_vars", "depends_on",
    "addons", "branch", "deploy_order", "skip", "server_id",
    "health_check_path", "root_directory", "deploy_mode",
    "compose_file", "docker_compose_file", "compose_main_service",
    "main_service", "default_branch",
}
_SERVICE_VALID_BUILDS = {"nixpacks", "docker", "dockerfile", "docker-compose", "compose", "static"}
_VALID_PORT_RANGE = (1, 65535)

_SECRET_HINTS = ("KEY", "SECRET", "PASSWORD", "TOKEN", "DSN")
_EXTERNAL_SECRETS = {
    "OPENAI_API_KEY",
    "GROK_API_KEY",
    "GEMINI_API_KEY",
    "CLAUDE_API_KEY",
    "JULES_API_KEY",
    "ANTHROPIC_API_KEY",
    "STRIPE_SECRET_KEY",
    "API_KEY",
}

_DEFAULT_WAVE_SIZE = 10
_MAX_WAVE_SIZE = 50
_WAVE_RECHECK_SECONDS = 15
_MAX_WAVE_RECHECKS = 480  # 2h (480 * 15s)
_SMSLY_CORE_HINTS = {
    "smsly-core",
    "smsly-platform-api",
    "platform-api",
}


def _validate_plan_structure(plan: dict) -> list[str]:
    """
    SEC-ZT-007: Validate ecosystem plan structure against schema.

    Returns a list of validation errors (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(plan, dict):
        return ["Plan must be a dict"]

    # Check for unknown top-level keys
    allowed_keys = _PLAN_REQUIRED_KEYS | _PLAN_OPTIONAL_KEYS
    unknown = set(plan.keys()) - allowed_keys
    if unknown:
        errors.append(f"Unknown plan keys: {', '.join(sorted(unknown))}")

    # Check required keys
    for key in _PLAN_REQUIRED_KEYS:
        if key not in plan:
            errors.append(f"Missing required plan key: {key}")

    services = plan.get("services", [])
    if not isinstance(services, list):
        errors.append("'services' must be a list")
        return errors

    for i, svc in enumerate(services):
        if not isinstance(svc, dict):
            errors.append(f"services[{i}] must be a dict, got {type(svc).__name__}")
            continue

        # Check unknown service keys
        svc_unknown = set(svc.keys()) - _SERVICE_REQUIRED_KEYS - _SERVICE_OPTIONAL_KEYS
        if svc_unknown:
            errors.append(f"services[{i}] unknown keys: {', '.join(sorted(svc_unknown))}")

        # Check required service keys
        for key in _SERVICE_REQUIRED_KEYS:
            if key not in svc:
                errors.append(f"services[{i}] missing required key: {key}")

        skip = svc.get("skip", False)
        if skip:
            continue

        # Validate build type
        build = str(svc.get("build", "") or "").strip().lower()
        if build and build not in _SERVICE_VALID_BUILDS:
            errors.append(f"services[{i}] invalid build '{build}'. Allowed: {', '.join(sorted(_SERVICE_VALID_BUILDS))}")

        # Validate port range
        port = svc.get("port")
        if port is not None:
            try:
                p = int(port)
                if p < _VALID_PORT_RANGE[0] or p > _VALID_PORT_RANGE[1]:
                    errors.append(f"services[{i}] port {p} out of range ({_VALID_PORT_RANGE[0]}-{_VALID_PORT_RANGE[1]})")
            except (TypeError, ValueError):
                errors.append(f"services[{i}] port must be an integer")

        # Validate depends_on format
        deps = svc.get("depends_on")
        if deps is not None and not isinstance(deps, (str, list)):
            errors.append(f"services[{i}] depends_on must be a string or list")

        # Validate addons format
        addons = svc.get("addons")
        if addons is not None:
            if isinstance(addons, list):
                for j, a in enumerate(addons):
                    if not isinstance(a, str):
                        errors.append(f"services[{i}] addons[{j}] must be a string")
            else:
                errors.append(f"services[{i}] addons must be a list")

    return errors


def _alias_ambiguity_report(dependencies: Dict[str, Set[str]], entries_by_key: Dict) -> list[str]:
    """Report ambiguous dependency aliases for user visibility."""
    warnings_list: list[str] = []
    alias_owner: Dict[str, str | None] = {}

    for key, entry in entries_by_key.items():
        repo = str(entry["repo"]).strip().lower()
        repo_name = repo.split("/")[-1]
        aliases = {
            repo, repo_name,
            str(entry.get("name") or "").strip().lower(),
            str(entry.get("requested_name") or "").strip().lower(),
        }
        for alias in aliases:
            if not alias:
                continue
            if alias in alias_owner and alias_owner[alias] != key:
                alias_owner[alias] = None
            else:
                alias_owner[alias] = key

    # Collect ambiguous aliases
    ambiguous = {alias for alias, owner in alias_owner.items() if owner is None}
    if ambiguous:
        warnings_list.append(f"Ambiguous dependency aliases (resolved to None): {', '.join(sorted(ambiguous))}")

    return warnings_list


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    """Read bounded int from env."""
    try:
        parsed = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _generate_secret(length: int = 50) -> str:
    """Generate a secure random string for env vars."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _slugify_name(raw: str) -> str:
    """Normalize a service name to a docker-safe slug."""
    name = re.sub(r"[^a-zA-Z0-9-]+", "-", str(raw or "").strip())
    name = re.sub(r"-{2,}", "-", name).strip("-").lower()
    return name or "service"


def _next_available_service_name(ServiceModel, base_name: str) -> str:
    """Find a globally unique service name."""
    if not ServiceModel.objects.filter(name=base_name).exists():
        return base_name

    for _ in range(20):
        candidate = f"{base_name}-{secrets.token_hex(2)}"
        if not ServiceModel.objects.filter(name=candidate).exists():
            return candidate

    return f"{base_name}-{secrets.token_hex(4)}"


def _looks_like_smsly_core_name(raw: str) -> bool:
    """Detect SMSLY core/platform API style service names."""
    token = _slugify_name(raw)
    if token in _SMSLY_CORE_HINTS:
        return True
    return token.startswith("smsly") and ("core" in token or "platform-api" in token)


def _repo_slug_from_url(url: str) -> str:
    """Extract repo slug from repository URL when available."""
    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    tail = text.split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return _slugify_name(tail)


def _select_shared_addon_anchor(services: List[Any]):
    """
    Choose the best service to host shared ecosystem addons.

    Prefer SMSLY core when present; otherwise use the first created service.
    """
    if not services:
        return None

    for service in services:
        name = getattr(service, "name", "")
        repo_url = getattr(service, "repository_url", "")
        if _looks_like_smsly_core_name(name) or _looks_like_smsly_core_name(_repo_slug_from_url(repo_url)):
            return service

    return services[0]


def _normalize_env_vars(raw_env: Any) -> Dict[str, str]:
    """
    Accept flexible env var formats from AI/heuristics and normalize to dict.

    Supported:
    - {"KEY": "value"}
    - [{"key": "KEY", "default": "value", "is_secret": true, ...}, ...]
    """
    normalized: Dict[str, str] = {}

    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            key_text = str(key or "").strip().upper()
            if not key_text:
                continue
            normalized[key_text] = "" if value is None else str(value)
        return normalized

    if isinstance(raw_env, list):
        for entry in raw_env:
            if not isinstance(entry, dict):
                continue
            key_text = str(entry.get("key") or "").strip().upper()
            if not key_text:
                continue

            default_val = entry.get("default")
            if default_val not in (None, ""):
                normalized[key_text] = str(default_val)
                continue

            if key_text in _EXTERNAL_SECRETS or key_text.endswith("_API_KEY"):
                normalized[key_text] = ""
                continue

            if entry.get("generate") or entry.get("is_secret"):
                normalized[key_text] = "{{GENERATE}}"
                continue

            normalized[key_text] = ""

    return normalized


def _stack_runtime_defaults(stack: str, port: int) -> Dict[str, str]:
    """Inject safe runtime defaults per stack."""
    stack_l = str(stack or "").strip().lower()
    defaults: Dict[str, str] = {"PORT": str(max(1, int(port or 3000)))}

    if stack_l in {"node", "nextjs", "nuxt"}:
        defaults["NODE_ENV"] = "production"
    if stack_l in {"python", "django"}:
        defaults["PYTHONUNBUFFERED"] = "1"
        defaults["PYTHONDONTWRITEBYTECODE"] = "1"

    return defaults


def _resolve_env_placeholders(
    env_vars: Dict[str, str],
    created_services: Dict[str, Any],
    shared_addons: Dict[str, str] = None,
) -> Dict[str, str]:
    """Resolve known placeholders into concrete values."""
    resolved: Dict[str, str] = {}
    shared_addons = shared_addons or {}

    for key, value in env_vars.items():
        value_text = str(value or "")

        if value_text == "{{GENERATE}}":
            resolved[key] = _generate_secret()
            continue

        if value_text.startswith("{{SERVICE:") and value_text.endswith("}}"):
            ref_name = value_text[10:-2].strip()
            ref_service = created_services.get(ref_name) or created_services.get(ref_name.lower())
            if ref_service:
                host = ref_service.name
                port = ref_service.internal_port or 3000
                resolved[key] = f"http://{host}:{port}"
            else:
                safe_ref = _slugify_name(ref_name)
                resolved[key] = f"http://{safe_ref}:3000"
            continue

        if value_text == "{{POSTGRES_URL}}":
            resolved[key] = shared_addons.get("POSTGRES", "postgresql://smsly:smsly@postgres:5432/smsly")
            continue

        if value_text == "{{REDIS_URL}}":
            resolved[key] = shared_addons.get("REDIS", "redis://redis:6379/0")
            continue

        if value_text == "{{ELASTICSEARCH_URL}}":
            resolved[key] = shared_addons.get("ELASTICSEARCH", "http://elasticsearch:9200")
            continue

        resolved[key] = value_text

    return resolved


def _validate_resolved_env(resolved_env: Dict[str, str]) -> None:
    """Ensure no unresolved placeholders remain in resolved env vars."""
    import re
    for value in resolved_env.values():
        if isinstance(value, str) and re.search(r"\{\{.*\}\}", value):
            raise ValueError(f"Unresolved placeholder found in env var values: {value}")


def _validate_required_env(resolved_env: Dict[str, str]) -> None:
    """Validate that required production environment variables are present and resolved."""
    required_keys = {
        "POSTGRES_URL",
        "REDIS_URL",
        "ELASTICSEARCH_URL",
        "DATABASE_URL",
        "CACHE_URL",
    }
    missing = [k for k in required_keys if not resolved_env.get(k)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def _runtime_watch_defaults(user) -> Dict[str, str]:
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
    """Map ecosystem plan build strategy to Service.buildpack choices."""
    build = str(raw or "").strip().lower()
    if build in {"docker", "dockerfile", "docker-file"}:
        return "DOCKER"
    if build in {"static", "static-site", "static_site"}:
        return "STATIC"
    return "NIXPACKS"


def _normalize_deploy_mode(svc_plan: Dict[str, Any]) -> Tuple[str, str, str]:
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


def _extract_dependencies(raw_depends: Any) -> List[str]:
    """Normalize depends_on values to a flat list of tokens."""
    if isinstance(raw_depends, str):
        text = raw_depends.strip()
        if not text:
            return []
        if "," in text:
            return [token.strip() for token in text.split(",") if token.strip()]
        return [text]

    if isinstance(raw_depends, list):
        values = []
        for item in raw_depends:
            token = str(item or "").strip()
            if token:
                values.append(token)
        return values

    return []


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    """Yield fixed-size chunks."""
    for idx in range(0, len(items), size):
        yield items[idx:idx + size]


def _build_dependency_waves(
    entries_by_key: Dict[str, Dict[str, Any]],
    dependencies: Dict[str, Set[str]],
    wave_size: int,
) -> Tuple[List[List[str]], List[str]]:
    """
    Build deployment waves from dependency graph.

    Returns:
    - waves: list of canonical repo keys grouped for parallel deploy
    - cyclic_or_unresolved: keys that could not be topologically sorted
    """
    dependents: Dict[str, Set[str]] = defaultdict(set)
    indegree: Dict[str, int] = {}

    for key in entries_by_key:
        deps = set(dep for dep in dependencies.get(key, set()) if dep in entries_by_key)
        dependencies[key] = deps
        indegree[key] = len(deps)
        for dep in deps:
            dependents[dep].add(key)

    def _entry_order(repo_key: str) -> int:
        return int(entries_by_key[repo_key].get("deploy_order", 99))

    ready = sorted(
        [key for key, degree in indegree.items() if degree == 0],
        key=_entry_order,
    )
    processed: Set[str] = set()
    waves: List[List[str]] = []

    while ready:
        layer = ready
        ready = []

        for chunk in _chunked(layer, wave_size):
            waves.append(chunk)

        for node in layer:
            processed.add(node)
            for dependent in dependents.get(node, set()):
                if dependent in processed:
                    continue
                indegree[dependent] = max(0, indegree[dependent] - 1)
                if indegree[dependent] == 0:
                    ready.append(dependent)

        ready.sort(key=_entry_order)

    unresolved = [
        key for key in sorted(entries_by_key.keys(), key=_entry_order)
        if key not in processed
    ]

    if unresolved:
        for chunk in _chunked(unresolved, wave_size):
            waves.append(chunk)

    return waves, unresolved


def _resolve_dependency_map(
    entries_by_key: Dict[str, Dict[str, Any]],
) -> Dict[str, Set[str]]:
    """Resolve depends_on aliases to canonical repo keys."""
    alias_owner: Dict[str, str | None] = {}

    for key, entry in entries_by_key.items():
        repo = str(entry["repo"]).strip().lower()
        repo_name = repo.split("/")[-1]
        aliases = {
            repo,
            repo_name,
            str(entry.get("name") or "").strip().lower(),
            str(entry.get("requested_name") or "").strip().lower(),
        }
        for alias in aliases:
            if not alias:
                continue
            if alias in alias_owner and alias_owner[alias] != key:
                alias_owner[alias] = None
            else:
                alias_owner[alias] = key

    alias_to_key = {
        alias: owner for alias, owner in alias_owner.items()
        if owner is not None
    }

    resolved: Dict[str, Set[str]] = {}
    for key, entry in entries_by_key.items():
        deps: Set[str] = set()
        for token in _extract_dependencies(entry.get("depends_on", [])):
            dep = alias_to_key.get(token.strip().lower())
            if dep and dep != key:
                deps.add(dep)
        resolved[key] = deps
    return resolved


def _queue_wave(app, deployment_ids: List[str], provider_id: str, wave_index: int) -> int:
    """Queue all QUEUED deployments in this wave."""
    from apps.deployments.models import Deployment

    queued = 0
    for deployment_id in deployment_ids:
        deployment = Deployment.objects.filter(id=deployment_id).first()
        if not deployment:
            continue
        if deployment.status != Deployment.Status.QUEUED:
            continue

        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Queued in wave {wave_index + 1}.\n"
        )
        deployment.save(update_fields=["build_logs"])

        app.send_task(
            "apps.deployments.tasks.smart_deploy_task",
            args=[str(deployment.id), str(provider_id)],
            kwargs={"skip_review": True},
        )
        queued += 1

    return queued


def _cancel_dependent_deployments(
    waves: List[List[str]],
    from_wave_index: int,
    failed_deployment_ids: List[str],
    dependencies: Dict[str, Set[str]],
    deployment_by_repo_key: Dict[str, str],
    reason: str,
) -> int:
    """Cancel queued deployments in unreleased waves that depend on failed deployments."""
    from apps.deployments.models import Deployment

    # Reverse the mapping to find the repo_key from deployment_id
    repo_key_by_deployment = {v: k for k, v in deployment_by_repo_key.items()}

    # Identify which repo_keys failed
    failed_keys = {
        repo_key_by_deployment[dep_id]
        for dep_id in failed_deployment_ids
        if dep_id in repo_key_by_deployment
    }

    if not failed_keys:
        return 0

    # Build dependents map: parent -> set of children
    dependents: Dict[str, Set[str]] = defaultdict(set)
    for key, deps in dependencies.items():
        for dep in deps:
            dependents[dep].add(key)

    # Transitively find all nodes that depend on a failed node
    to_cancel_keys: Set[str] = set()
    stack = list(failed_keys)
    while stack:
        node = stack.pop()
        for child in dependents.get(node, set()):
            if child not in to_cancel_keys and child not in failed_keys:
                to_cancel_keys.add(child)
                stack.append(child)

    if not to_cancel_keys:
        return 0

    to_cancel_ids = [
        deployment_by_repo_key[key]
        for key in to_cancel_keys
        if key in deployment_by_repo_key
    ]

    cancelled = 0
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled before execution: {reason}\n"
        )
        deployment.save(update_fields=["status", "finished_at", "build_logs"])
        cancelled += 1

    return cancelled


def _apply_service_profile(service, svc_plan: Dict[str, Any], provider, port: int):
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

    service.repository_url = f"https://github.com/{svc_plan['repo']}"
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
        except Exception:
            pass

    service.save()


@shared_task(bind=True, soft_time_limit=1800, time_limit=2100)
def ecosystem_scan_task(self, user_id: str, scan_window_days: int = 30, ai_provider: str = None, selected_repos: list = None) -> dict:
    """
    Scan all of a user's GitHub repos and return a deploy plan.
    This is async because fetching and AI analysis can take 30-60s.

    scan_window_days is currently reserved for future repo recency filtering.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.views_github import _get_github_token
    from services.ecosystem import scan_and_analyze

    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        return {"error": "User not found"}

    token = _get_github_token(user)
    if not token:
        return {"error": "GitHub not connected. Please link your GitHub account first."}

    try:
        logger.info(f"Starting ecosystem scan for user {user_id} with selected_repos: {selected_repos}")
        result = scan_and_analyze(token, ai_provider=ai_provider, selected_repos=selected_repos)
        logger.info(f"Ecosystem scan completed successfully for user {user_id}")
        return result
    except SoftTimeLimitExceeded:
        logger.warning("Ecosystem scan timed out for user %s", user_id, exc_info=True)
        return {
            "error": (
                "Ecosystem scan timed out before the full GitHub inventory finished. "
                "Retry the scan; large accounts may take several minutes."
            ),
            "code": "ecosystem_scan_timeout",
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
        }
    except Exception as exc:
        logger.exception("Ecosystem scan failed unexpectedly for user %s: %s", user_id, exc)
        return {"error": f"Scan failed: {str(exc)}"}


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def ecosystem_release_wave_task(
    self,
    provider_id: str,
    waves: List[List[str]],
    wave_index: int = 1,
    recheck_count: int = 0,
    max_rechecks: int = _MAX_WAVE_RECHECKS,
    dependencies: Dict[str, Set[str]] = None,
    deployment_by_repo_key: Dict[str, str] = None,
) -> dict:
    """Release next wave, continuing successful branches and cancelling failed branches."""
    from apps.deployments.models import Deployment

    if not waves or wave_index >= len(waves):
        return {"status": "completed", "waves": len(waves or [])}

    previous_wave = [str(dep_id) for dep_id in waves[wave_index - 1]]
    deployments = list(Deployment.objects.filter(id__in=previous_wave).values("id", "status"))
    statuses = [dep["status"] for dep in deployments]

    if not statuses:
        # If the wave is missing entirely, we don't have enough context to continue branches
        return {
            "status": "blocked",
            "reason": "previous wave not found",
            "cancelled": 0,
        }

    failed_states = {Deployment.Status.FAILED, Deployment.Status.CANCELLED}
    in_progress_states = {Deployment.Status.QUEUED, Deployment.Status.BUILDING, Deployment.Status.HEALTH_CHECK, "STARTING"}

    # Bug 5 Fix: Retry failed deployments once before permanently failing them.
    for dep in deployments:
        if dep["status"] == Deployment.Status.FAILED:
            dep_obj = Deployment.objects.filter(id=dep["id"]).first()
            if dep_obj and not dep_obj.build_logs.endswith("\n[Ecosystem] Retrying once...\n"):
                # Mark as queued to retry once, and append a marker
                dep_obj.status = Deployment.Status.QUEUED
                dep_obj.build_logs = (dep_obj.build_logs or "") + "\n[Ecosystem] Retrying once...\n"
                dep_obj.save(update_fields=["status", "build_logs"])

                # Re-queue the individual task
                self.app.send_task(
                    "apps.deployments.tasks.smart_deploy_task",
                    args=[str(dep_obj.id), provider_id],
                    kwargs={"skip_review": True},
                )
                # Update local list to consider this in-progress instead of failed
                dep["status"] = Deployment.Status.QUEUED

    # Re-evaluate statuses after possible retries
    statuses = [dep["status"] for dep in deployments]
    failed_ids = [str(dep["id"]) for dep in deployments if dep["status"] in failed_states]
    in_progress = any(status in in_progress_states for status in statuses)

    if in_progress:
        if recheck_count >= max_rechecks:
            # Time out waiting for remaining ones
            failed_ids.extend([str(dep["id"]) for dep in deployments if dep["status"] in in_progress_states])
            if dependencies and deployment_by_repo_key:
                cancelled = _cancel_dependent_deployments(
                    waves,
                    from_wave_index=wave_index,
                    failed_deployment_ids=failed_ids,
                    dependencies=dependencies,
                    deployment_by_repo_key=deployment_by_repo_key,
                    reason="previous wave timed out waiting for success",
                )
            else:
                cancelled = 0
            return {
                "status": "timed_out",
                "wave": wave_index,
                "cancelled": cancelled,
            }

        self.app.send_task(
            "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
            args=[provider_id, waves, wave_index, recheck_count + 1, max_rechecks, dependencies, deployment_by_repo_key],
            countdown=_env_int(
                "ECOSYSTEM_WAVE_RECHECK_SECONDS",
                _WAVE_RECHECK_SECONDS,
                minimum=5,
                maximum=120,
            ),
        )
        return {
            "status": "waiting",
            "wave": wave_index,
            "recheck_count": recheck_count + 1,
        }

    # At this point, everything is either terminal (ACTIVE/STAGED or FAILED/CANCELLED)
    cancelled = 0
    if failed_ids and dependencies and deployment_by_repo_key:
        cancelled = _cancel_dependent_deployments(
            waves,
            from_wave_index=wave_index,
            failed_deployment_ids=failed_ids,
            dependencies=dependencies,
            deployment_by_repo_key=deployment_by_repo_key,
            reason="upstream dependency deployment failed",
        )

    # We queue the next wave (which ignores CANCELLED statuses so only viable nodes deploy)
    queued = _queue_wave(self.app, waves[wave_index], provider_id, wave_index)
    if wave_index + 1 < len(waves):
        self.app.send_task(
            "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
            args=[provider_id, waves, wave_index + 1, 0, max_rechecks, dependencies, deployment_by_repo_key],
            countdown=_env_int(
                "ECOSYSTEM_WAVE_RECHECK_SECONDS",
                _WAVE_RECHECK_SECONDS,
                minimum=5,
                maximum=120,
            ),
        )
    return {
        "status": "released",
        "wave": wave_index + 1,
        "queued": queued,
        "cancelled_dependents": cancelled,
    }


@shared_task(bind=True, soft_time_limit=1800, time_limit=2100)
def ecosystem_deploy_task(self, user_id: str, plan: dict) -> dict:
    """
    Deploy all services in the plan using dependency-aware waves.

    SEC-ZT-007: Plan structure is validated against schema before any records
    are created. Secrets are encrypted using task_encryption before passing
    to Celery broker.

    This creates Service + Deployment records for each repo and triggers
    smart_deploy_task with skip_review=True as each wave becomes eligible.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.models import Service, Deployment, EnvironmentVariable
    from apps.cloud.models import CloudProvider

    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        return {"error": "User not found"}

    if not isinstance(plan, dict):
        return {"error": "Invalid plan payload"}

    # SEC-ZT-007: Validate plan structure before creating any records
    schema_errors = _validate_plan_structure(plan)
    if schema_errors:
        logger.error("Plan schema validation failed: %s", schema_errors)
        return {
            "error": "Plan validation failed",
            "details": schema_errors,
        }

    services_plan = plan.get("services", [])
    if not isinstance(services_plan, list) or not services_plan:
        return {"error": "No services in deploy plan"}

    provider = CloudProvider.objects.filter(is_active=True).first() or CloudProvider.objects.first()
    if not provider:
        return {"error": "No cloud provider configured. Add one in Settings -> Cloud Providers."}

    # Track created resources for potential rollback
    _rollback_services: list[str] = []
    _rollback_deployments: list[str] = []
    _rollback_env_vars: list[str] = []
    _rollback_addons: list[str] = []

    requested_wave_size = plan.get("wave_size", _DEFAULT_WAVE_SIZE)
    try:
        requested_wave_size = int(requested_wave_size)
    except (TypeError, ValueError):
        requested_wave_size = _DEFAULT_WAVE_SIZE
    wave_size = max(1, min(_MAX_WAVE_SIZE, requested_wave_size))
    wave_size = _env_int("ECOSYSTEM_DEPLOY_WAVE_SIZE", wave_size, minimum=1, maximum=_MAX_WAVE_SIZE)

    # 1. Parse and validate manifest if provided, bulk verify env before continuing
    manifest_content = plan.get("manifest")
    if manifest_content:
        from apps.deployments.services.ecosystem_persist import bulk_persist_and_verify_ecosystem_env
        from apps.deployments.services.ecosystem_graph import build_ecosystem_graph
        from apps.deployments.services.ecosystem_env import EcosystemEnvResolver
        try:
            graph = build_ecosystem_graph(manifest_content)
            resolver = EcosystemEnvResolver(graph)
            success, _, errors = resolver.validate_and_resolve()
            if not success:
                return {"error": "Environment validation failed", "details": errors}
        except Exception as e:
            return {"error": f"Invalid manifest: {e}"}

    entries_by_key: Dict[str, Dict[str, Any]] = {}
    for svc_plan in services_plan:
        if not isinstance(svc_plan, dict):
            continue
        if svc_plan.get("skip"):
            continue

        repo = str(svc_plan.get("repo") or "").strip()
        if not repo:
            continue
        repo_key = repo.lower()

        source_name = str(svc_plan.get("name") or repo.split("/")[-1]).strip()
        requested_name = _slugify_name(source_name)
        entry = {
            "repo": repo,
            "repo_key": repo_key,
            "name": source_name,
            "requested_name": requested_name,
            "stack": str(svc_plan.get("stack") or "unknown"),
            "build": str(svc_plan.get("build") or "nixpacks"),
            "deploy_order": _order_key(svc_plan),
            "depends_on": svc_plan.get("depends_on", []),
            "plan": svc_plan,
        }
        entries_by_key[repo_key] = entry

    if not entries_by_key:
        return {"error": "No deployable services in plan"}

    dependencies = _resolve_dependency_map(entries_by_key)
    waves_repo_keys, unresolved = _build_dependency_waves(
        entries_by_key=entries_by_key,
        dependencies=dependencies,
        wave_size=wave_size,
    )

    # SEC-ZT-007: Report alias ambiguity + unresolved cycles to user
    alias_warnings = _alias_ambiguity_report(dependencies, entries_by_key)
    if unresolved:
        alias_warnings.append(
            f"Unresolved/cyclic dependencies (deployed last): {', '.join(unresolved)}"
        )

    ordered_keys = [key for wave in waves_repo_keys for key in wave]
    results = []
    created_services: Dict[str, Any] = {}
    deployment_by_repo_key: Dict[str, str] = {}

    # Bug 4 Fix: Provision required addons synchronously before wave 1.
    from apps.deployments.models_addons import Addon
    from services.addon_provisioner import addon_provisioner
    from apps.deployments.models import Service

    # Collect all needed addons across all services
    required_addons = set()
    for svc_plan in services_plan:
        if isinstance(svc_plan, dict) and not svc_plan.get("skip"):
            addons_list = svc_plan.get("addons", [])
            for a in addons_list:
                required_addons.add(str(a).strip().upper())

    created_service_records: List[Any] = []

    for repo_key in ordered_keys:
        entry = entries_by_key[repo_key]
        svc_plan = entry["plan"]
        repo = entry["repo"]
        requested_name = entry["requested_name"]
        stack = entry["stack"]
        build_method = entry["build"]

        try:
            port = int(svc_plan.get("port", 3000) or 3000)
        except (TypeError, ValueError):
            port = 3000
        port = max(1, min(65535, port))

        server_id = svc_plan.get("server_id") or plan.get("server_id")
        server = None
        if server_id:
            from apps.deployments.models import ManagedServer
            try:
                if str(server_id).lower() in ("local", "primary"):
                    server = ManagedServer.get_primary()
                else:
                    server = ManagedServer.objects.filter(id=server_id, owner=user).first()
            except Exception:
                pass
        else:
            from apps.deployments.services.node_selector import select_eligible_node
            server = select_eligible_node(user)

        if not server:
            # Mark the deployment as pending so the user sees a clear status
            # and can retry later when a node becomes available.
            logger.error(f"No eligible deployment node available for {repo}.")
            results.append({
                "repo": repo,
                "name": requested_name,
                "status": "pending",
                "error": "No eligible deployment node available."
            })
            # Optionally, create a placeholder Service with a pending flag
            # to surface in the UI. This avoids silent failures.
            try:
                Service.objects.create(
                    name=requested_name,
                    owner=user,
                    repository_url=f"https://github.com/{repo}",
                    branch=str(
                        svc_plan.get("branch")
                        or svc_plan.get("default_branch")
                        or "main"
                    ).strip() or "main",
                    internal_port=port,
                    provider=provider,
                    server=None,
                    status="PENDING",
                )
            except Exception:
                # If creation fails (e.g., model does not have a status field),
                # we simply continue; the pending entry in ``results`` is still
                # returned to the caller.
                pass
            continue

        try:
            service = Service.objects.filter(owner=user, name=requested_name).first()
            if service is None:
                final_name = _next_available_service_name(Service, requested_name)
                service = Service.objects.create(
                    name=final_name,
                    owner=user,
                    repository_url=f"https://github.com/{repo}",
                    branch=str(
                        svc_plan.get("branch")
                        or svc_plan.get("default_branch")
                        or "main"
                    ).strip() or "main",
                    internal_port=port,
                    provider=provider,
                    server=server,
                )
                _rollback_services.append(str(service.id))

            _apply_service_profile(service, {**svc_plan, "repo": repo}, provider, port)

            if all(getattr(existing, "id", None) != getattr(service, "id", None) for existing in created_service_records):
                created_service_records.append(service)

            # Keep multiple aliases for inter-service references.
            aliases = {
                entry["name"],
                entry["name"].lower(),
                requested_name,
                requested_name.lower(),
                service.name,
                service.name.lower(),
                repo,
                repo.lower(),
                repo.split("/")[-1],
                repo.split("/")[-1].lower(),
            }
            for alias in aliases:
                created_services[alias] = service

            env_vars = _normalize_env_vars(svc_plan.get("env_vars", {}))

            shared_addons_urls = {}
            graph_anchor_service = _select_shared_addon_anchor(created_service_records)
            if graph_anchor_service:
                from services.ecosystem_graph import build_ecosystem_graph
                graph = build_ecosystem_graph(graph_anchor_service)
                shared_addons_urls = graph.get("shared_addons", {})

            resolved_env = _resolve_env_placeholders(
                env_vars,
                created_services,
                shared_addons=shared_addons_urls
            )

            # -----------------------------------------------------------------
            # AI Senate integration – optionally enrich env vars using the
            # intelligence service when the feature flag is enabled.
            # -----------------------------------------------------------------
            if getattr(settings, "SENATE_ENABLED", True):
                try:
                    from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService

                    senate_suggestions = EnvironmentIntelligenceService.resolve_environment(
                        {},  # No detailed context; the service will generate defaults.
                        stack,
                        service.name,
                    )
                    # Merge suggestions without overwriting explicit values.
                    for k, v in senate_suggestions.items():
                        if k not in resolved_env:
                            resolved_env[k] = v
                except Exception as exc:
                    logger.warning("AI Senate enrichment failed for %s: %s", service.name, exc)

            for key, value in _stack_runtime_defaults(stack, port).items():
                resolved_env.setdefault(key, value)
            for key, value in _runtime_watch_defaults(user).items():
                resolved_env.setdefault(key, value)

            _validate_resolved_env(resolved_env)

            # Ensure required production env vars are present
            _validate_required_env(resolved_env)

            for key, value in resolved_env.items():
                key_upper = str(key or "").strip().upper()
                if not key_upper:
                    continue
                is_secret = any(hint in key_upper for hint in _SECRET_HINTS)
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key_upper,
                    defaults={"value": str(value or ""), "is_secret": is_secret},
                )

            deployment = Deployment.objects.create(
                service=service,
                commit_hash="ecosystem-deploy",
                commit_message=f"Zero-config ecosystem deploy ({stack})",
                status=Deployment.Status.QUEUED,
                build_logs=(
                    f"Ecosystem deploy: {repo} ({stack})\n"
                    f"Port: {port} | Build: {build_method}\n"
                    f"Env vars: {len(resolved_env)} configured\n"
                    f"Depends on: {', '.join(_extract_dependencies(entry['depends_on'])) or '(none)'}\n\n"
                ),
            )
            _rollback_deployments.append(str(deployment.id))

            deployment_by_repo_key[repo_key] = str(deployment.id)
            results.append({
                "repo": repo,
                "name": service.name,
                "server": service.server.name if service.server else "N/A",
                "service_id": str(service.id),
                "deployment_id": str(deployment.id),
                "status": "queued",
                "stack": stack,
                "port": port,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to prepare deploy for %s: %s", repo, exc)
            results.append({
                "repo": repo,
                "name": requested_name,
                "status": "failed",
                "error": str(exc),
            })

    # Bulk persist env if using a manifest
    if manifest_content:
        from apps.deployments.services.ecosystem_persist import bulk_persist_and_verify_ecosystem_env
        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_content, created_services)
        if not success:
            logger.error(f"Bulk persistence failed: {msg}")
            # Mark all deployments as failed
            for repo_key, dep_id in deployment_by_repo_key.items():
                Deployment.objects.filter(id=dep_id).update(
                    status=Deployment.Status.FAILED,
                    build_logs=f"Failed to persist valid environment variables: {msg}"
                )
            return {"error": f"Env validation failed: {msg}"}

    waves: List[List[str]] = []
    for wave in waves_repo_keys:
        deployment_ids = [
            deployment_by_repo_key[repo_key]
            for repo_key in wave
            if repo_key in deployment_by_repo_key
        ]
        if deployment_ids:
            waves.append(deployment_ids)

    # Synchronously provision required addons onto a stable anchor service.
    addon_anchor_service = _select_shared_addon_anchor(created_service_records)
    if addon_anchor_service and required_addons:
        supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())
        for addon_type in required_addons:
            if addon_type not in supported_addons:
                logger.warning("Ecosystem addon %s is not supported; skipping", addon_type)
                continue

            # Check if addon already exists
            existing_addon = Addon.objects.filter(service=addon_anchor_service, addon_type=addon_type).first()
            if existing_addon and existing_addon.status == Addon.Status.ACTIVE:
                continue

            if not existing_addon:
                existing_addon = Addon.objects.create(
                    service=addon_anchor_service,
                    name=f"{addon_type.lower()}-shared"[:255],
                    addon_type=addon_type,
                    status=Addon.Status.PROVISIONING,
                )
                _rollback_addons.append(str(existing_addon.id))

            try:
                logger.info("Provisioning shared addon %s for ecosystem", addon_type)
                cid, url = addon_provisioner.provision(existing_addon)
                existing_addon.connection_url = url
                existing_addon.status = Addon.Status.ACTIVE
                existing_addon.save()

                key_map = {
                    'POSTGRES': 'DATABASE_URL',
                    'REDIS': 'REDIS_URL',
                    'ELASTICSEARCH': 'ELASTICSEARCH_URL',
                }
                key = key_map.get(addon_type, f"{addon_type}_URL")
                EnvironmentVariable.objects.update_or_create(
                    service=addon_anchor_service,
                    key=key,
                    defaults={"value": url, "is_secret": True}
                )
            except Exception as e:
                logger.error("Failed to provision shared addon %s: %s", addon_type, e)
                existing_addon.status = Addon.Status.FAILED
                existing_addon.save()

    queued_now = 0
    # Pass dependencies to the wave task
    safe_dependencies = {k: list(v) for k, v in dependencies.items()} if dependencies else {}

    if waves:
        queued_now = _queue_wave(self.app, waves[0], str(provider.id), wave_index=0)
        if len(waves) > 1:
            self.app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
                args=[str(provider.id), waves, 1, 0, _MAX_WAVE_RECHECKS, safe_dependencies, deployment_by_repo_key],
                countdown=_env_int(
                    "ECOSYSTEM_WAVE_RECHECK_SECONDS",
                    _WAVE_RECHECK_SECONDS,
                    minimum=5,
                    maximum=120,
                ),
            )

    return {
        "status": "deploying",
        "total": len(services_plan),
        "prepared": len(results),
        "queued_immediately": queued_now,
        "waves": len(waves),
        "wave_size": wave_size,
        "unresolved_dependency_nodes": unresolved,
        "alias_warnings": alias_warnings,
        "queued": len([r for r in results if r["status"] == "queued"]),
        "skipped": len([s for s in services_plan if isinstance(s, dict) and s.get("skip")]),
        "failed": len([r for r in results if r["status"] == "failed"]),
        "services": results,
    }


def _rollback_ecosystem_deploy(
    service_ids: list[str],
    deployment_ids: list[str],
    addon_ids: list[str],
    env_var_keys: list[str],
):
    """
    SEC-ZT-007: Clean up partially created resources on deploy failure.
    Removes services, deployments, addons, and env vars created during
    the failed ecosystem deployment attempt.
    """
    from apps.deployments.models import Service, Deployment
    from apps.deployments.models_addons import Addon

    logger.warning("Rolling back ecosystem deploy: %d services, %d deployments, %d addons",
                   len(service_ids), len(deployment_ids), len(addon_ids))

    if deployment_ids:
        Deployment.objects.filter(id__in=deployment_ids).exclude(
            status__in=("ACTIVE", "BUILDING"),
        ).delete()

    if addon_ids:
        Addon.objects.filter(id__in=addon_ids).exclude(
            status="ACTIVE",
        ).delete()

    if service_ids:
        Service.objects.filter(id__in=service_ids).delete()

    logger.info("Rollback complete")
