import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from apps.deployments.tasks.ecosystem.constants import (
    _DEFAULT_WAVE_SIZE,
    _PLAN_REQUIRED_KEYS,
    _SERVICE_REQUIRED_KEYS,
    _SERVICE_VALID_BUILDS,
    _VALID_PORT_RANGE,
)

from .env_vars import _service_placeholder_refs
from .repo import (
    _canonical_repo_ref,
    _slugify_name,
)

logger = logging.getLogger(__name__)


def _validate_plan_structure(plan: dict) -> list[str]:
    """
    Validate ecosystem plan structure.
    Returns a list of validation errors (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(plan, dict):
        return ["Plan must be a dict"]

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


def _alias_ambiguity_report(dependencies: dict[str, set[str]], entries_by_key: dict) -> list[str]:
    """Report ambiguous dependency aliases for user visibility."""
    warnings_list: list[str] = []
    alias_owner: dict[str, str | None] = {}

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


def _extract_dependencies(raw_depends: Any) -> list[str]:
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


def _chunked(items: list[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size chunks."""
    for idx in range(0, len(items), size):
        yield items[idx:idx + size]


def _build_dependency_waves(
    entries_by_key: dict[str, dict[str, Any]],
    dependencies: dict[str, set[str]],
    wave_size: int,
) -> tuple[list[list[str]], list[str]]:
    """
    Build deployment waves from dependency graph.

    Returns:
    - waves: list of canonical repo keys grouped for parallel deploy
    - cyclic_or_unresolved: keys that could not be topologically sorted
    """
    dependents: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {}

    for key in entries_by_key:
        try:
            raw_deps = dependencies.get(key, set())
            # Ensure all dependencies are hashable (strings)
            safe_deps = []
            for dep in raw_deps:
                if dep in entries_by_key:
                    try:
                        # Ensure dependency is a string
                        str_dep = str(dep)
                        safe_deps.append(str_dep)
                    except Exception:
                        logger.warning(f"Cannot convert dependency {dep} to string for service {key}")
                        continue

            deps = set(safe_deps)
            dependencies[key] = deps
            indegree[key] = len(deps)
            for dep in deps:
                dependents[dep].add(key)
        except Exception as e:
            logger.error(f"Error processing dependencies for {key}: {e}")
            # Skip this entry to prevent the entire scan from failing
            dependencies[key] = set()
            indegree[key] = 0

    def _entry_order(repo_key: str) -> int:
        return int(entries_by_key[repo_key].get("deploy_order", 99))

    ready = sorted(
        [key for key, degree in indegree.items() if degree == 0],
        key=_entry_order,
    )
    processed: set[str] = set()
    waves: list[list[str]] = []

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
    entries_by_key: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """Resolve depends_on aliases to canonical repo keys."""
    alias_owner: dict[str, str | None] = {}

    for key, entry in entries_by_key.items():
        repo = str(entry["repo"]).strip().lower()
        repo_name = repo.split("/")[-1]
        aliases = {
            repo,
            repo_name,
            str(entry.get("name") or "").strip().lower(),
            str(entry.get("requested_name") or "").strip().lower(),
            _slugify_name(entry.get("name") or "").lower(),
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

    resolved: dict[str, set[str]] = {}
    for key, entry in entries_by_key.items():
        deps: set[str] = set()
        raw_tokens = [
            *_extract_dependencies(entry.get("depends_on", [])),
            *_service_placeholder_refs(entry.get("plan", {}).get("env_vars", {})),
        ]
        for token in raw_tokens:
            token_text = token.strip().lower()
            dep = (
                alias_to_key.get(token_text)
                or alias_to_key.get(_canonical_repo_ref(token_text).lower())
                or alias_to_key.get(_slugify_name(token_text).lower())
            )
            if dep and dep != key:
                deps.add(dep)
        resolved[key] = deps
    return resolved
