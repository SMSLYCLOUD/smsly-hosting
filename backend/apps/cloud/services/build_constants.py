"""Shared constants for the build pipeline (Docker and Nixpacks builders).

Both ``builder.py`` (Nixpacks CLI wrapper) and ``pipeline.py`` (Docker build)
need to decide which env vars are safe to pass as build args and which must be
withheld until runtime.  Keeping the rules in one place prevents drift.
"""

# ---------------------------------------------------------------------------
# Env-var suffixes that indicate a value is a secret.
#
# We match on **suffixes** (e.g. var.upper().endswith(...)) rather than
# substrings so that innocent vars like ``PUBLIC_KEY_PATH``, ``TOKEN_TYPE``,
# or ``USE_DSN_FORMAT`` are not accidentally filtered out.
# ---------------------------------------------------------------------------
BUILD_SECRET_SUFFIXES: tuple[str, ...] = (
    "_SECRET",
    "_KEY",
    "_PASSWORD",
    "_TOKEN",
    "_DSN",
    "_SALT",
    "_CREDENTIAL",
    "_CREDENTIALS",
)

# Full env-var names that are always secrets, regardless of suffix.
# Connection URLs that embed credentials in the value.
BUILD_SECRET_EXACT_NAMES: frozenset[str] = frozenset({
    "SECRET",
    "SECRET_KEY",
    "API_KEY",
    "DATABASE_URL",
    "POSTGRES_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "JWT_SECRET",
    "JWT",
    "DSN",
    "PASSWORD",
    "TOKEN",
    "CREDENTIAL",
})

# Prefixes that mark a var as explicitly safe for build-time injection.
# These are public frontend vars that frameworks require at build time
# (e.g. NEXT_PUBLIC_STRIPE_KEY is Stripe's *publishable* key, not secret).
_SAFE_BUILD_PREFIXES: tuple[str, ...] = (
    "NEXT_PUBLIC_",
    "VITE_",
    "PUBLIC_",
    "NUXT_PUBLIC_",
    "REACT_APP_",
    "GATSBY_",
    "EXPO_PUBLIC_",
)


def is_secret_env_var(name: str) -> bool:
    """Return True if *name* should be withheld from Docker/Nixpacks builds.

    Safe vars like ``NEXT_PUBLIC_STRIPE_KEY``, ``VITE_API_URL``, or
    ``PUBLIC_KEY_PATH`` will **not** be flagged because:
    1. Framework public prefixes are always considered safe.
    2. We match on suffixes and exact names rather than arbitrary substrings.

    >>> is_secret_env_var("DATABASE_URL")
    True
    >>> is_secret_env_var("NEXT_PUBLIC_API_URL")
    False
    >>> is_secret_env_var("NEXT_PUBLIC_STRIPE_KEY")
    False
    >>> is_secret_env_var("JWT_SECRET")
    True
    >>> is_secret_env_var("PUBLIC_KEY_PATH")
    False
    """
    upper = name.upper()

    # Framework public prefixes are always safe — they are designed to be
    # embedded in client-side bundles and must be present at build time.
    if any(upper.startswith(prefix) for prefix in _SAFE_BUILD_PREFIXES):
        return False

    if upper in BUILD_SECRET_EXACT_NAMES:
        return True
    return any(upper.endswith(suffix) for suffix in BUILD_SECRET_SUFFIXES)
