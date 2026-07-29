import ipaddress
import os
import re


def caddy_disabled_mode() -> bool:
    mode = str(os.environ.get("MODE", "")).strip().lower()
    node_type = str(os.environ.get("NODE_TYPE", "")).strip().lower()
    return mode in {"agent", "agent-lite", "node"} or node_type in {
        "agent",
        "agent-lite",
        "node",
    }


def is_agent_lite() -> bool:
    mode = str(os.environ.get("MODE", "")).strip().lower()
    node_type = str(os.environ.get("NODE_TYPE", "")).strip().lower()
    return mode in {"agent", "agent-lite"} or node_type in {"agent", "agent-lite"}


def _table_exists(table_name: str) -> bool:
    from django.db import connection
    try:
        return table_name in connection.introspection.table_names()
    except Exception:
        return False


def _is_ip(domain: str) -> bool:
    if not domain:
        return False
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return False


def _normalize_upstream_ip(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_interface(value).ip)
    except ValueError:
        return value.split("/", 1)[0].strip()


def _normalize_caddy_site_label(label: str) -> str:
    value = str(label or "").strip().strip(",")
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    if value.startswith("[") and "]" in value:
        return value
    if ":" in value and not value.startswith(":"):
        value = value.split(":", 1)[0]
    return value.strip().lower().rstrip(".")
