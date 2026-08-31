"""Centralized env value sanitizer.

Single source of truth for stripping AI / template / markdown leakage from
environment variable values before they are persisted to the DB or written
to a container's ``.env`` file.

The previous logic lived inline in
``apps.intelligence.services.env_intelligence`` and was incomplete:
backticks (`` ` ``), smart-quote wrappers, ``{{...}}`` template tokens,
JS-style trailing comments, and ``ALLOWED_HOSTS=*`` defaults all leaked
through into container env files, crashing pydantic / JSON parsers and
exposing admin hosts to the public.

Every place that accepts a value from the user, the AI, or a template
MUST call :func:`sanitize_env_value` before persisting or rendering it.
"""
from __future__ import annotations

import re
from typing import Final


# ---------------------------------------------------------------------------
# 1. Reject outright: literal placeholders that should never reach a container
# ---------------------------------------------------------------------------
# Anything in this set is dropped by the runtime / DB scrubber, and the
# caller is expected to fall back to a safe default.
PLACEHOLDER_EXACT: Final[frozenset[str]] = frozenset({
    "", " ", "-", "_", "n/a", "na", "none", "null", "nil", "tbd", "todo",
    "changeme", "CHANGEME", "TODO", "TBD", "FILL_ME", "fill_me", "Fill_Me",
    "YOUR_API_KEY", "your_api_key", "YOUR_SECRET_KEY", "your_secret_key",
    "YOUR_TOKEN", "your_token",
    "GENERATE", "generate", "Generate",
    "{GENERATE}", "{{GENERATE}}", "{FILL_ME}", "{{FILL_ME}}",
    # Markdown/HTML leak patterns seen from AI Senate
    "<CHANGE_ME>", "<change_me>", "<CHANGE_ME_HERE>",
    "REPLACE_ME", "REPLACE_WITH_VALUE",
    # The literal template text we saw in GATEWAY_IPS
    "<list-of-trusted-gateway-IP/CIDR>",
    "<list-of-trusted-gateway-ip/cidr>",
    "list-of-trusted-gateway-IP/CIDR",
    # Production-unsafe defaults
    "*",  # CORS / ALLOWED_HOSTS wildcard — caller must provide an explicit allow-list
})

# Placeholders that survive only as prefixes/substrings of a value
PLACEHOLDER_PREFIXES: Final[tuple[str, ...]] = (
    "REPLACE_WITH_", "YOUR_", "REPLACE_ME__",
    "<CHANGE_ME", "<REPLACE_ME", "<TODO", "<TBD",
)

# Mock / dev / localhost patterns that the AI Senate sometimes injects
# even when a production value is expected. These are scrubbed and replaced
# with a platform default (handled by the caller, here we just flag them).
_MOCK_PATTERNS: Final[tuple[str, ...]] = (
    "localhost:8080", "127.0.0.1:8080",
)


# ---------------------------------------------------------------------------
# 2. Tokens that *wrap* a value and must be stripped
# ---------------------------------------------------------------------------
# We strip these greedily, repeatedly, because the AI sometimes nests them
# (e.g. ``"`'value'"``).
_WRAPPER_CHARS = ("`", "'", '"', "\u2018", "\u2019", "\u201c", "\u201d")


# ---------------------------------------------------------------------------
# 3. Trailing-comment leak: AI sometimes returns
#        `https://app.example.com",  // production‑safe default`
#    which is valid as a Traefik Host() label but crashes pydantic JSON.
#    Detect and strip from the first ``",`` or ``//`` or ``/*`` onward.
# ---------------------------------------------------------------------------
_TRAILING_COMMENT_RE = re.compile(
    r'"\s*,\s*//.*$|"//.*$|/\*.*\*/|"\s*#.*$', re.DOTALL,
)
# Detect a JS-style line comment that follows a closing quote
_TRAILING_JS_COMMENT_RE = re.compile(
    r'^(?P<v>.*?)(?:"\s*)?(?://|/\*).*$', re.DOTALL,
)

# Reject values that contain a literal newline (would break ``.env`` files
# which are line-oriented). We replace newlines with spaces.
_NEWLINE_RE = re.compile(r"[\r\n]+")


# ---------------------------------------------------------------------------
# 4. ALLOWED_HOSTS / CORS safety
# ---------------------------------------------------------------------------
# If a user (or the AI) submits ``*`` we REPLACE it with a same-origin-only
# default rather than allowing wildcard. Wildcard is a security risk for
# Django, CORS, and CSP — every framework warns against it.
def _safe_allowed_hosts_default() -> str:
    """Internal-only safe default for ALLOWED_HOSTS / CORS_ALLOWED_ORIGINS.

    Empty value means "no public host allowed" which forces the operator to
    supply a real allow-list at the Caddy / Traefik layer.
    """
    return ""


def _default_for_key(key: str) -> str | None:
    """Return a safe default for known keys when the user-provided value is
    a placeholder or ``*``. Return ``None`` if no default is known (caller
    will drop the value entirely)."""
    if not key:
        return None
    k = key.upper()
    if k in ("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS"):
        # Wildcard is dangerous. Force the operator to be explicit.
        return _safe_allowed_hosts_default()
    if k in ("CORS_ALLOWED_ORIGINS", "CORS_ORIGINS", "CORS_DEV_ORIGINS", "ALLOWED_ORIGINS"):
        return _safe_allowed_hosts_default()
    if k == "GATEWAY_IPS":
        return "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"  # RFC1918 only
    if k == "TRUSTED_NETWORKS":
        return "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    if k == "TRUSTED_PROXIES":
        return "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    if k == "ENVIRONMENT":
        return "production"
    if k == "LOG_LEVEL":
        return "info"
    if k in ("NODE_ENV",):
        return "production"
    if k in ("DEBUG", "TESTING", "DJANGO_DEBUG"):
        return "false"
    if k == "WEB_CONCURRENCY":
        return "4"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_placeholder(value: str | None) -> bool:
    """True if *value* is a literal placeholder / template token that should
    never reach a container."""
    if value is None:
        return True
    v = value.strip()
    if not v:
        return True
    if v in PLACEHOLDER_EXACT:
        return True
    if v.startswith(PLACEHOLDER_PREFIXES):
        return True
    # {{...}} wrappers
    if v.startswith("{{") and v.endswith("}}"):
        return True
    # <placeholder> wrappers
    if v.startswith("<") and v.endswith(">") and " " not in v and "/" not in v:
        return True
    return False


def looks_wildcard_host(value: str | None) -> bool:
    """True if *value* is a single ``*`` or starts with ``*`` — i.e. an
    open-wildcard for ALLOWED_HOSTS / CORS."""
    if not value:
        return False
    return value.strip() in {"*", "*/*", "*  ", "*,*"} or value.strip().startswith("*,*")


def _strip_wrappers(v: str) -> str:
    prev = None
    while prev != v:
        prev = v
        v = v.strip()
        for ch in _WRAPPER_CHARS:
            if len(v) >= 2 and v[0] == ch and v[-1] == ch:
                v = v[1:-1]
        v = v.strip()
    return v


def _strip_trailing_comment(v: str) -> str:
    """Strip a trailing JS / C-style comment that follows a closing quote.

    ``https://app.example.com",  // production‑safe default``
    becomes ``https://app.example.com``.

    We do NOT strip ``#`` comments in general because legitimate values can
    contain ``#`` — only the JS / C-style sequences the AI leaks.
    """
    m = _TRAILING_COMMENT_RE.search(v)
    if m:
        v = v[:m.start()].rstrip()
    m = _TRAILING_JS_COMMENT_RE.match(v)
    if m:
        v = (m.group("v") or "").rstrip().rstrip('"').rstrip(",").rstrip()
    return v


def sanitize_env_value(
    value: str | None,
    key: str | None = None,
    *,
    allow_empty: bool = True,
) -> str | None:
    """Return a safe, container-ready value.

    Returns ``None`` if the value is a placeholder / empty AND the caller
    does not have a safe default. If *allow_empty* is True and the input
    is empty/placeholder AND we have a known default for *key*, that
    default is returned. If the default itself is empty, ``""`` is returned
    (the operator must set an explicit value).

    Rules (applied in order):
      1. None / empty -> empty string (after default substitution).
      2. Strip balanced wrapper quotes / backticks / smart quotes.
      3. Strip ``{{...}}`` and ``<...>`` template wrappers.
      4. Strip ``// ...`` and ``/* ... */`` comments that follow a quote.
      5. Collapse newlines (would break .env file format).
      6. If value is a literal placeholder, return the key-specific
         default (or ``""`` / ``None``).
      7. If value is a wildcard ``*`` for ALLOWED_HOSTS / CORS, return
         the safe (empty) default.
    """
    if value is None:
        raw = ""
    else:
        raw = str(value)

    v = raw.strip()

    # 1. Empty
    if not v:
        if allow_empty:
            default = _default_for_key(key or "")
            return default if default is not None else ""
        return None

    # 2. Strip balanced wrappers
    v = _strip_wrappers(v)

    # 3. Strip {{...}} / <...> template wrappers
    if v.startswith("{{") and v.endswith("}}"):
        v = v[2:-2].strip()
        v = _strip_wrappers(v)
    if v.startswith("<") and v.endswith(">") and " " not in v and "/" not in v[1:-1]:
        v = v[1:-1].strip()
        v = _strip_wrappers(v)

    # 4. Strip trailing comments
    v = _strip_trailing_comment(v)
    v = v.strip()
    v = _strip_wrappers(v)

    # 5. Collapse newlines (would break .env file format)
    if _NEWLINE_RE.search(v):
        v = _NEWLINE_RE.sub(" ", v).strip()

    # 6. Literal placeholder
    if v in PLACEHOLDER_EXACT:
        default = _default_for_key(key or "")
        return default if default is not None else ""

    # Anything still matching a placeholder prefix -> placeholder
    if v.startswith(PLACEHOLDER_PREFIXES):
        default = _default_for_key(key or "")
        return default if default is not None else ""

    # Mock / dev patterns in production keys
    if v in _MOCK_PATTERNS:
        default = _default_for_key(key or "")
        if default is not None:
            return default

    # 7. Wildcard rejection
    if looks_wildcard_host(v) and key:
        k = key.upper()
        if k in (
            "ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS",
            "CORS_ALLOWED_ORIGINS", "CORS_ORIGINS", "CORS_DEV_ORIGINS",
            "ALLOWED_ORIGINS",
        ):
            return _safe_allowed_hosts_default()

    return v


def sanitize_for_env_file(value: str | None) -> str:
    """Return a value that's safe to drop into a ``.env`` file (line-oriented).

    Replaces any newline / NUL with a space, and escapes ``#`` if it appears
    at the start of the value (env files treat leading ``#`` as comments).
    """
    v = sanitize_env_value(value, allow_empty=True)
    if v is None:
        return ""
    v = v.replace("\x00", " ")
    v = _NEWLINE_RE.sub(" ", v)
    if v.startswith("#"):
        v = "\\" + v
    return v
