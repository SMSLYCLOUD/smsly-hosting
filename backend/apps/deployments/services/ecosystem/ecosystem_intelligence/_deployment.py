import logging
from collections import defaultdict

from ._addons import _rebuild_addons_manifest
from ._normalization import _coerce_addons, _coerce_depends_on
from ._utils import _safe_order, _repo_short_name

logger = logging.getLogger(__name__)


def _build_deploy_sequence(services: list[dict]) -> list[str]:
    """Build deploy sequence from dependency-aware topological sort."""
    try:
        name_map: dict[str, dict] = {}
        for svc in services:
            if isinstance(svc, dict) and not svc.get("skip"):
                name = str(svc.get("name") or _repo_short_name(svc))
                name_map[name] = svc

        # Build adjacency + in-degree
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
