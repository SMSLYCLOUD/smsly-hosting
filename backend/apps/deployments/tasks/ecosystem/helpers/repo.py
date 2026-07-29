import logging

logger = logging.getLogger(__name__)
import re
from typing import Any
from urllib.parse import urlparse

from apps.deployments.tasks.ecosystem.constants import (
    _SMSLY_CORE_HINTS,
)


def _canonical_repo_ref(raw: Any) -> str:
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("git@github.com:"):
        text = text.split(":", 1)[1]
    else:
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            text = parsed.path.strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.strip("/")


def _repository_url(repo_ref: Any) -> str:
    raw = str(repo_ref or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return raw.rstrip("/")
    canonical = _canonical_repo_ref(raw)
    return f"https://github.com/{canonical}" if canonical else ""


def _repo_short_name(repo_ref: Any) -> str:
    canonical = _canonical_repo_ref(repo_ref)
    return canonical.split("/")[-1] if canonical else ""


def _slugify_name(raw: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9-]+", "-", str(raw or "").strip())
    name = re.sub(r"-{2,}", "-", name).strip("-").lower()
    return name or "service"


def _repo_slug_from_url(url: str) -> str:
    """Extract repo slug from repository URL when available."""
    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    tail = text.split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return _slugify_name(tail)


def _looks_like_smsly_core_name(raw: str) -> bool:
    """Detect SMSLY core/platform API style service names."""
    token = _slugify_name(raw)
    if token in _SMSLY_CORE_HINTS:
        return True
    return token.startswith("smsly") and ("core" in token or "platform-api" in token)
