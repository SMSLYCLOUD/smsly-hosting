import logging
from collections import defaultdict
from typing import Any

from .addons import _coerce_addons
from .helpers import _coerce_depends_on, _repo_short_name, _safe_order

logger = logging.getLogger(__name__)


def _build_deploy_sequence(services: list[dict]) -> list[str]:
    try:
        name_map: dict[str, dict] = {}
        for svc in services:
            if isinstance(svc, dict) and not svc.get("skip"):
                name = str(svc.get("name") or _repo_short_name(svc))
                name_map[name] = svc

        deps: dict[str, set[str]] = {}
        for name, svc in name_map.items():
            raw = _coerce_depends_on(svc.get("depends_on", []) or [])
            resolved = set()
            for d in raw:
                if d in name_map:
                    resolved.add(d)
            deps[name] = resolved

        indegree: dict[str, int] = {n: len(deps[n]) for n in name_map}
        dependents: dict[str, list[str]] = defaultdict(list)
        for name, dep_set in deps.items():
            for d in dep_set:
                dependents[d].append(name)

        ready = sorted(
            [n for n, deg in indegree.items() if deg == 0],
            key=lambda n: (_safe_order(name_map[n].get("deploy_order"), 99), n),
        )
        ordered: list[str] = []
        processed: set[str] = set()

        while ready:
            node = ready.pop(0)
            ordered.append(node)
            processed.add(node)
            for dependent in dependents.get(node, []):
                if dependent in processed:
                    continue
                indegree[dependent] = max(0, indegree[dependent] - 1)
                if indegree[dependent] == 0:
                    ready.append(dependent)
            ready.sort(key=lambda n: (_safe_order(name_map[n].get("deploy_order"), 99), n))

        unresolved = [n for n in name_map if n not in processed]
        ordered.extend(unresolved)

        return ["addons", *ordered]

    except Exception as e:
        logger.warning("Deploy sequence build failed: %s", e)
        try:
            return ["addons"] + [
                str(svc.get("name") or _repo_short_name(svc))
                for svc in services
                if isinstance(svc, dict) and not svc.get("skip")
            ]
        except Exception:
            return ["addons"]


def _rebuild_addons_manifest(services: list[dict], existing_addons: Any) -> list[dict]:
    addon_map: dict[str, set] = {}

    if isinstance(existing_addons, list):
        for addon in existing_addons:
            if not isinstance(addon, dict):
                continue
            addon_types = _coerce_addons(addon)
            addon_type = addon_types[0] if addon_types else ""
            if not addon_type:
                continue
            addon_map.setdefault(addon_type, set())
            for svc_name in _coerce_depends_on(addon.get("shared_by", []) or []):
                svc_text = str(svc_name or "").strip()
                if svc_text:
                    try:
                        addon_map[addon_type].add(svc_text)
                    except TypeError:
                        logger.warning("Unhashable svc_text for addon %r: %r", addon_type, svc_text)

    for service in services:
        if not isinstance(service, dict) or service.get("skip"):
            continue
        service_name = str(service.get("name") or _repo_short_name(service)).strip()
        if not service_name:
            continue
        normalized_addons = _coerce_addons(service.get("addons", []) or [])
        service["addons"] = normalized_addons
        for addon_type in normalized_addons:
            if not addon_type:
                continue
            try:
                str_addon_type = str(addon_type)
                str_service_name = str(service_name)
                addon_map.setdefault(str_addon_type, set()).add(str_service_name)
            except TypeError as e:
                logger.warning("Unhashable addon_type or service_name: %r / %r - %s", addon_type, service_name, e)
            except Exception as e:
                logger.warning("Unexpected error processing addon {0} for service {1}: {2}", addon_type, service_name, e)

    try:
        return [
            {"type": addon_type, "shared_by": sorted(shared_by)}
            for addon_type, shared_by in sorted(addon_map.items())
        ]
    except TypeError as exc:
        logger.warning("Unhashable key in addon_map: %s", exc)
        return []
