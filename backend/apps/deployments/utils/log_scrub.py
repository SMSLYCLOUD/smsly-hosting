"""Shared secret-masking for user-facing log payloads.

Build / runtime / addon container logs routinely echo connection URLs,
query strings, and CLI output that embed credentials
(``postgresql://user:PASSWORD@host/db``, ``?token=SECRET``,
``Authorization: Bearer SECRET``). Those payloads are served through
REST responses and WebSocket streams to browsers, where they land in
DOM text, copy buffers, and proxy access logs.

:func:`mask_secrets_in_text` redacts the credential material while
keeping the surrounding log line intact so the output stays useful
for debugging. It is deliberately conservative:

- Only well-shaped credential patterns are masked (userinfo in a
  URI, known secret-ish query params, ``key: value``/``key=value``
  secret assignments). Ordinary log text is never altered.
- It never raises: on any internal error the input is returned
  unchanged (fail-open on masking, so logging can never break a
  request or a build; callers stay fail-closed everywhere else).
"""

from __future__ import annotations

import re

# ``scheme://user:PASSWORD@host`` — the password group only. The
# username is kept: it identifies *which* credential leaked without
# exposing the secret itself. The username may be empty
# (``redis://:PASSWORD@host`` is the common Redis form).
_URL_USERINFO_RE = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@?#]*:)([^/\s@?#]+)(@)"
)

# ``?token=SECRET`` / ``&password=SECRET`` in URLs and bare query
# strings. Values run to the next delimiter.
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|api[_-]?key|secret|password|passwd|pwd|access[_-]?key|auth|session[_-]?id)[^=]*=)([^&\s;,\}]+)"
)

# ``Authorization: Bearer SECRET``, ``password = SECRET``,
# ``api_key:SECRET`` in free-form log lines. The value stops at
# whitespace AND at ``&`` so a masked query tail (``?token=***&x=1``)
# is not swallowed into the secret.
_KV_SECRET_RE = re.compile(
    r"(?i)((?:authorization\s*[:=]\s*(?:bearer\s+)?"
    r"|(?:api[_-]?key|token|secret|password|passwd|access[_-]?key)\s*[:=]\s*)"
    r")([^\s,;\}\{'\"&]+)"
)


def mask_secrets_in_text(text: str | None) -> str | None:
    """Mask tokens/passwords embedded in *text* (usually log output).

    Returns the masked string, or the input unchanged when there is
    nothing to mask (including ``None``/empty input). Never raises.
    """
    if not text:
        return text
    try:
        masked = _URL_USERINFO_RE.sub(r"\1***\3", text)
        masked = _URL_QUERY_SECRET_RE.sub(r"\1***", masked)
        masked = _KV_SECRET_RE.sub(r"\1***", masked)
        return masked
    except Exception:
        return text
