"""Helpers for deterministic service runtime names."""
from __future__ import annotations

import re


def normalize_runtime_name(raw: str) -> str:
    value = str(raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "service"


def get_service_runtime_name(service) -> str:
    """Return canonical docker-safe runtime name for a Service-like object."""
    for attr in ("normalized_name", "service_name", "name"):
        candidate = getattr(service, attr, None)
        if candidate:
            return normalize_runtime_name(candidate)
    return "service"
