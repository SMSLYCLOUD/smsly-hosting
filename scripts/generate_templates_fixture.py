#!/usr/bin/env python3
"""
Generate backend templates fixture JSON.

Why:
- The frontend App Store page expects a list of templates from `/api/v1/templates/`.
- The backend currently serves `backend/apps/deployments/fixtures/templates.json`.
- Keeping a generator makes it easy to scale the catalog (e.g., 1000 templates) deterministically.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# When executed as a script, Python puts `scripts/` on sys.path, not the repo root.
# Ensure `backend/` is importable (we rely on backend.services.app_templates).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _is_secret_key(key: str) -> bool:
    key_u = key.upper()
    secret_markers = ("PASSWORD", "SECRET", "TOKEN", "PRIVATE", "API_KEY", "RPC_SECRET", "MASTER_KEY")
    if any(marker in key_u for marker in secret_markers):
        return True
    # URLs may embed credentials; err on the safe side.
    if key_u in ("DATABASE_URL", "REDIS_URL", "MYSQL_URL", "MONGODB_URI"):
        return True
    return False


def _icon_for(template_id: str, category: str) -> str:
    # Keep this conservative: icons are purely for UI. Use stable CDN URLs.
    known = {
        "postgres": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/postgresql/postgresql-original.svg",
        "redis": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/redis/redis-original.svg",
        "mysql": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/mysql/mysql-original.svg",
        "mongodb": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/mongodb/mongodb-original.svg",
        "grafana": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/grafana/grafana-original.svg",
        "prometheus": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/prometheus/prometheus-original.svg",
    }
    if template_id in known:
        return known[template_id]

    by_category = {
        "database": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/postgresql/postgresql-original.svg",
        "cms": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/wordpress/wordpress-original.svg",
        "analytics": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/grafana/grafana-original.svg",
        "dev-tools": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/docker/docker-original.svg",
        "smsly-ecosystem": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/docker/docker-original.svg",
    }
    return by_category.get(category, "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/docker/docker-original.svg")


def build_base_templates() -> list[dict[str, Any]]:
    # Import from the backend registry (works when run from repo root).
    from backend.services.app_templates import APP_TEMPLATES

    base: list[dict[str, Any]] = []
    for t in sorted(APP_TEMPLATES.values(), key=lambda x: x.id):
        # Convert dataclass -> dict, then reshape into the fixture schema.
        td = asdict(t)
        env_vars = [
            {"key": k, "value": v, "is_secret": _is_secret_key(k)}
            for k, v in sorted((td.get("env_vars") or {}).items())
        ]

        entry: dict[str, Any] = {
            "id": td["id"],
            "name": td["name"],
            "description": td["description"],
            "icon": _icon_for(td["id"], td["category"]),
            "category": td["category"],
            # In the current fixture schema, this is shown as a URL reference; use docs_url if available.
            "repository_url": td.get("docs_url") or "https://hub.docker.com/",
            "docker_image": td["docker_image"],
            "default_port": td["default_port"],
            # Optional: used by one-click deploy to pre-provision dependencies.
            "required_addons": list(td.get("required_addons") or []),
        }
        if env_vars:
            entry["env_vars"] = env_vars
        base.append(entry)
    return base


def build_templates(count: int) -> list[dict[str, Any]]:
    base = build_base_templates()
    if not base:
        raise RuntimeError("No base templates found (APP_TEMPLATES is empty).")

    out: list[dict[str, Any]] = []
    for i in range(count):
        b = base[i % len(base)]
        variant = i // len(base)
        if variant == 0:
            out.append(b)
            continue

        n = f"{variant:02d}"
        entry = dict(b)
        entry["id"] = f"{b['id']}-p{n}"
        entry["name"] = f"{b['name']} Preset {n}"
        entry["description"] = f"{b['description']} (preset {n})"
        out.append(entry)

    # Ensure unique IDs (defensive).
    ids = [t["id"] for t in out]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Generated duplicate template IDs.")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/apps/deployments/fixtures/templates.json"),
    )
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be >= 1")

    templates = build_templates(args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(templates, indent=2, ensure_ascii=True)
    # Always end with a newline for clean diffs.
    args.output.write_text(payload + "\n", encoding="utf-8")
    print(f"Wrote {len(templates)} templates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
