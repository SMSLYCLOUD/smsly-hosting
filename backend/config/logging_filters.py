"""Logging filters for Secrets hygiene.

The Caddy on-demand TLS ask callback authenticates via a ``?secret=``
query parameter (Caddy's ``ask`` directive cannot send headers). Django's
``django.request`` logger renders the full request path, which would
otherwise land the shared secret in container logs, Loki, and any log
shipper in plaintext (2026-09-11: the CADDY_ASK_SECRET was visible in
`docker logs` output). This filter masks it everywhere it flows.
"""
import logging
import re

_SECRET_RE = re.compile(r"(secret=)([^&\s'\"]+)", re.IGNORECASE)


class RedactQuerySecretsFilter(logging.Filter):
    """Replace ``secret=<value>`` with ``secret=[REDACTED]`` in log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: ANN001
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = _SECRET_RE.sub(r"\1[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
