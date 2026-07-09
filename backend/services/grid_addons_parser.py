"""Grid Addons Parser.

Parses and validates ``grid.addons`` manifest files found in service
repositories.  The manifest declares both standard addon dependencies
(Postgres, Redis, …) and custom infrastructure bundles (Kamailio,
FreeSWITCH, …) that Grid provisions alongside the service.

File format: YAML or JSON, no extension required (the file is simply
named ``grid.addons``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StandardAddonDecl:
    """A standard addon dependency (POSTGRES, REDIS, …)."""
    name: str
    addon_type: str


@dataclass(frozen=True)
class BundleServiceDecl:
    """One service inside a custom bundle."""
    name: str
    image: str | None = None
    repo: str | None = None
    branch: str | None = None
    build: str | None = None          # "dockerfile" | "nixpacks"
    dockerfile: str | None = None     # path relative to repo root
    context: str | None = None        # build context relative to repo root
    ports: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)  # alias accepted
    healthcheck: dict[str, Any] | None = None
    labels: list[str] = field(default_factory=list)
    cap_add: list[str] = field(default_factory=list)
    command: str | list[str] | None = None
    depends_on: list[str] = field(default_factory=list)
    restart: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def env_vars(self) -> dict[str, str]:
        """Merge ``environment`` and ``env`` dicts (``env`` wins)."""
        merged = dict(self.environment)
        merged.update(self.env)
        return merged

    @property
    def source_type(self) -> str:
        if self.image:
            return "image"
        if self.repo:
            return "repo"
        return "unknown"


@dataclass(frozen=True)
class BundleDecl:
    """A named group of services that form an infrastructure bundle."""
    name: str
    network: str | None = None
    services: list[BundleServiceDecl] = field(default_factory=list)
    backup: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GridAddonsManifest:
    """Parsed representation of a ``grid.addons`` file."""
    version: str
    service_type: str | None
    addons: list[StandardAddonDecl]
    bundles: list[BundleDecl]
    raw: dict[str, Any]

    @property
    def standard_addon_types(self) -> set[str]:
        return {a.addon_type for a in self.addons}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_GRID_ADDONS_FILENAME = "grid.addons"


def find_grid_addons_file(repo_path: str) -> str | None:
    """Return the absolute path to ``grid.addons`` if it exists, else ``None``."""
    candidate = os.path.join(repo_path, _GRID_ADDONS_FILENAME)
    if os.path.isfile(candidate):
        return candidate
    return None


def load_grid_addons(repo_path: str) -> GridAddonsManifest | None:
    """Load and parse ``grid.addons`` from *repo_path*.

    Returns ``None`` when the file is absent.  Raises ``ValueError`` on
    invalid content.
    """
    path = find_grid_addons_file(repo_path)
    if path is None:
        return None

    # Reject excessively large files (1 MB limit)
    file_size = os.path.getsize(path)
    if file_size > 1_048_576:
        raise ValueError(
            f"grid.addons file is too large ({file_size} bytes, max 1 MB)"
        )

    with open(path, encoding="utf-8") as fh:
        raw_text = fh.read()

    return parse_grid_addons(raw_text, source_path=path)


def parse_grid_addons(raw_text: str, source_path: str = "<inline>") -> GridAddonsManifest:
    """Parse raw YAML/JSON text into a :class:`GridAddonsManifest`.

    Raises ``ValueError`` on schema violations.
    """
    data = _load_data(raw_text, source_path)
    return _build_manifest(data, source_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_data(raw_text: str, source_path: str) -> dict:
    """Detect YAML vs JSON and return the parsed dict."""
    stripped = raw_text.strip()
    # YAML first — handles flow syntax {key: value} and [items] correctly
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required to parse grid.addons.  "
            "Install it with: pip install pyyaml"
        ) from None

    try:
        result = yaml.safe_load(stripped)
    except yaml.YAMLError:
        result = None

    if result is not None:
        if not isinstance(result, dict):
            raise ValueError(
                f"grid.addons must be a YAML/JSON mapping (dict), "
                f"got {type(result).__name__} in {source_path}"
            )
        return result

    # Fallback to JSON (only if YAML produced nothing)
    if stripped.startswith(("{", "[")):
        try:
            result = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {source_path}: {exc}") from exc
        if not isinstance(result, dict):
            raise ValueError(
                f"grid.addons must be a YAML/JSON mapping (dict), "
                f"got {type(result).__name__} in {source_path}"
            )
        return result

    raise ValueError(f"Could not parse grid.addons from {source_path}")


def _build_manifest(data: dict, source_path: str) -> GridAddonsManifest:
    """Validate and build a :class:`GridAddonsManifest` from raw dict."""
    version = str(data.get("version", "1"))
    if version not in ("1",):
        raise ValueError(f"Unsupported grid.addons version: {version} (file: {source_path})")

    service_type = data.get("service_type")
    addons = _parse_addons(data.get("addons", {}), source_path)
    bundles = _parse_bundles(data.get("bundles", {}), source_path)

    return GridAddonsManifest(
        version=version,
        service_type=service_type,
        addons=addons,
        bundles=bundles,
        raw=data,
    )


def _parse_addons(raw: Any, source_path: str) -> list[StandardAddonDecl]:
    """Parse the ``addons`` mapping."""
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ValueError(f"'addons' must be a mapping in {source_path}")

    result: list[StandardAddonDecl] = []
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            cfg = {}
        addon_type = str(cfg.get("type", name)).upper()
        result.append(StandardAddonDecl(name=str(name), addon_type=addon_type))
    return result


def _parse_bundles(raw: Any, source_path: str) -> list[BundleDecl]:
    """Parse the ``bundles`` mapping."""
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ValueError(f"'bundles' must be a mapping in {source_path}")

    result: list[BundleDecl] = []
    for bundle_name, bundle_cfg in raw.items():
        if not isinstance(bundle_cfg, dict):
            raise ValueError(
                f"Bundle '{bundle_name}' must be a mapping in {source_path}"
            )

        network = bundle_cfg.get("network")
        services = _parse_bundle_services(
            bundle_cfg.get("services", {}), bundle_name, source_path
        )
        backup = bundle_cfg.get("backup", {}) or {}

        result.append(BundleDecl(
            name=str(bundle_name),
            network=network,
            services=services,
            backup=backup,
        ))
    return result


def _parse_bundle_services(raw: Any, bundle_name: str, source_path: str) -> list[BundleServiceDecl]:
    """Parse the ``services`` mapping inside a bundle."""
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ValueError(
            f"'services' in bundle '{bundle_name}' must be a mapping in {source_path}"
        )

    result: list[BundleServiceDecl] = []
    for svc_name, svc_cfg in raw.items():
        if not isinstance(svc_cfg, dict):
            raise ValueError(
                f"Service '{svc_name}' in bundle '{bundle_name}' must be a mapping"
            )

        image = svc_cfg.get("image")
        repo = svc_cfg.get("repo")

        if not image and not repo:
            raise ValueError(
                f"Service '{svc_name}' in bundle '{bundle_name}' must have "
                f"either 'image' or 'repo' ({source_path})"
            )

        # Validate build config
        if repo and svc_cfg.get("build"):
            build_type = str(svc_cfg["build"]).lower()
            if build_type not in ("dockerfile", "nixpacks"):
                raise ValueError(
                    f"Service '{svc_name}' build type must be 'dockerfile' or "
                    f"'nixpacks', got '{build_type}' ({source_path})"
                )

        ports = svc_cfg.get("ports", [])
        if isinstance(ports, str):
            ports = [ports]

        volumes = svc_cfg.get("volumes", [])
        if isinstance(volumes, str):
            volumes = [volumes]

        environment = svc_cfg.get("environment", {})
        if not isinstance(environment, dict):
            raise ValueError(
                f"Service '{svc_name}' environment must be a dict, "
                f"got {type(environment).__name__} ({source_path})"
            )
        env = svc_cfg.get("env", {})
        if not isinstance(env, dict):
            raise ValueError(
                f"Service '{svc_name}' env must be a dict, "
                f"got {type(env).__name__} ({source_path})"
            )

        labels = svc_cfg.get("labels", [])
        if isinstance(labels, str):
            labels = [labels]

        cap_add = svc_cfg.get("cap_add", [])
        if isinstance(cap_add, str):
            cap_add = [cap_add]

        depends_on = svc_cfg.get("depends_on", [])
        if isinstance(depends_on, str):
            depends_on = [depends_on]

        command = svc_cfg.get("command")

        healthcheck = svc_cfg.get("healthcheck")
        if healthcheck is not None and not isinstance(healthcheck, dict):
            raise ValueError(
                f"Service '{svc_name}' healthcheck must be a dict, "
                f"got {type(healthcheck).__name__} ({source_path})"
            )

        result.append(BundleServiceDecl(
            name=str(svc_name),
            image=image,
            repo=repo,
            branch=svc_cfg.get("branch"),
            build=svc_cfg.get("build"),
            dockerfile=svc_cfg.get("dockerfile"),
            context=svc_cfg.get("context"),
            ports=ports,
            volumes=volumes,
            environment=environment,
            env=env,
            healthcheck=svc_cfg.get("healthcheck"),
            labels=labels,
            cap_add=cap_add,
            command=command,
            depends_on=depends_on,
            restart=svc_cfg.get("restart"),
            extra={
                k: v
                for k, v in svc_cfg.items()
                if k not in {
                    "image", "repo", "branch", "build", "dockerfile",
                    "context", "ports", "volumes", "environment", "env",
                    "healthcheck", "labels", "cap_add", "command",
                    "depends_on", "restart",
                }
            },
        ))
    return result


# ---------------------------------------------------------------------------
# Template variable resolution
# ---------------------------------------------------------------------------

def resolve_variables(
    value: str,
    context: dict[str, str],
    max_depth: int = 5,
) -> str:
    """Resolve ``{{var}}`` and ``${var}`` template references in *value*.

    References are looked up in *context*.  Iterative resolution handles
    transitive references (A → B → C) up to *max_depth* passes.
    Unresolved references are left as-is.
    """
    import re
    _TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}|\$\{(.+?)\}")

    def _replace(match: re.Match) -> str:
        key = (match.group(1) or match.group(2)).strip()
        return context.get(key, match.group(0))

    result = value
    for _ in range(max_depth):
        new_result = _TEMPLATE_RE.sub(_replace, result)
        if new_result == result:
            break  # no more substitutions
        result = new_result
    return result


def resolve_bundle_env(
    services: list[BundleServiceDecl],
    addon_urls: dict[str, str],
) -> list[BundleServiceDecl]:
    """Return new service decls with env vars resolved against *addon_urls*.

    *addon_urls* maps addon names (e.g. ``"postgres"``) to their
    connection URLs.  The resolution context also includes per-field
    entries like ``{{addons.postgres.url}}``.
    """
    context: dict[str, str] = {}
    for addon_name, url in addon_urls.items():
        context[f"addons.{addon_name}.url"] = url
        # Also provide short alias
        context[f"{addon_name}_url"] = url

    resolved: list[BundleServiceDecl] = []
    for svc in services:
        new_env = {
            k: resolve_variables(str(v), context)
            for k, v in svc.env_vars.items()
        }
        resolved.append(BundleServiceDecl(
            name=svc.name,
            image=svc.image,
            repo=svc.repo,
            branch=svc.branch,
            build=svc.build,
            dockerfile=svc.dockerfile,
            context=svc.context,
            ports=svc.ports,
            volumes=svc.volumes,
            environment=new_env,
            env={},
            healthcheck=svc.healthcheck,
            labels=svc.labels,
            cap_add=svc.cap_add,
            command=svc.command,
            depends_on=svc.depends_on,
            restart=svc.restart,
            extra=svc.extra,
        ))
    return resolved
