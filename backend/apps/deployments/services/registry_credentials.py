"""Per-project registry credential auto-provisioning.

The platform registry uses htpasswd auth (registry:2.8.3, bcrypt).
Historically every project shared ONE platform credential
(smsly-registry / REGISTRY_PASSWORD). This module gives each project
its OWN auto-generated credential:

  - username: proj-<first 8 of project uuid>  (stable per project)
  - password: 32-byte urlsafe token, stored Encrypted on the
    ScopedRegistry row
  - bcrypt line appended to /auth/htpasswd (shared volume mount,
    writable by the backend container; the registry reads it live)

Why per-project:
  - revocation is scoped (delete the htpasswd line + rotate the row)
  - audit trails name the exact tenant in registry auth failures
  - the display on the project page can SAFELY reveal the project's
    own credential to the project owner without exposing the platform
    master credential

Fallbacks: if /auth/htpasswd is not writable (non-compose install,
read-only mount), the caller falls back to the platform credential —
credentials remain functional, just not per-project.
"""
from __future__ import annotations

import logging
import re
import secrets

logger = logging.getLogger(__name__)

_HTPASSWD_PATH = "/auth/htpasswd"
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}$")


def _htpasswd_writable() -> bool:
    import os
    return os.access(_HTPASSWD_PATH, os.W_OK)


def _bcrypt_hash(password: str) -> str | None:
    """Return an apache-htpasswd-compatible bcrypt hash, or None."""
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    except Exception:
        pass
    # Fallback: container has no bcrypt lib — use passlib if present
    try:
        from passlib.hash import apr_md5_crypt  # type: ignore
        return apr_md5_crypt.hash(password)
    except Exception:
        return None


def project_registry_username(project_id) -> str:
    return f"proj-{str(project_id).replace('-', '')[:8]}"


def upsert_htpasswd_user(username: str, password: str) -> bool:
    """Add or replace ONE user line in /auth/htpasswd atomically.

    The registry reads htpasswd per-request, so no restart is needed.
    Returns True on success, False when the file is unavailable.
    """
    import os
    import tempfile

    if not _htpasswd_writable():
        logger.debug("htpasswd not writable at %s — skipping upsert", _HTPASSWD_PATH)
        return False

    hashed = _bcrypt_hash(password)
    if not hashed:
        logger.warning("No bcrypt implementation available — cannot upsert %s", username)
        return False

    new_line = f"{username}:{hashed}\n"
    try:
        existing = ""
        if os.path.exists(_HTPASSWD_PATH):
            with open(_HTPASSWD_PATH) as f:
                existing = f.read()

        kept = [
            ln for ln in existing.splitlines()
            if ln.strip() and not ln.split(":", 1)[0] == username
        ]
        kept.append(new_line.strip())

        # Atomic write via tmpfile + rename in the same directory
        dir_ = os.path.dirname(_HTPASSWD_PATH)
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".htpasswd-", text=True)
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(kept) + "\n")
            os.chmod(tmp, 0o664)
            os.replace(tmp, _HTPASSWD_PATH)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        logger.info("Upserted registry credential %s into htpasswd", username)
        return True
    except Exception as exc:
        logger.error("Failed to upsert htpasswd user %s: %s", username, exc)
        return False


def remove_htpasswd_user(username: str) -> bool:
    """Remove a user line from /auth/htpasswd (credential revocation)."""
    import os
    import tempfile

    if not os.path.exists(_HTPASSWD_PATH) or not _htpasswd_writable():
        return False
    try:
        with open(_HTPASSWD_PATH) as f:
            existing = f.read()
        kept = [
            ln for ln in existing.splitlines()
            if ln.strip() and ln.split(":", 1)[0] != username
        ]
        if len(kept) == len(existing.splitlines()):
            return True  # nothing to remove
        dir_ = os.path.dirname(_HTPASSWD_PATH)
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".htpasswd-", text=True)
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(kept) + "\n")
            os.chmod(tmp, 0o664)
            os.replace(tmp, _HTPASSWD_PATH)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:
        logger.error("Failed to remove htpasswd user %s: %s", username, exc)
        return False


def ensure_project_registry_credentials(project) -> dict:
    """Get or create per-project registry credentials.

    Returns {'username', 'password', 'per_project': bool, 'urls': [...]}.
    Falls back to the platform credential when htpasswd is not writable
    (per_project=False, still fully functional for push/pull).
    """
    from django.contrib.contenttypes.models import ContentType

    from apps.deployments.models.registry_scope import ScopedRegistry
    from apps.deployments.services.registry_routing import (
        internal_registry_hosts,
        master_registry_node_url,
    )

    ct = ContentType.objects.get_for_model(project)
    scoped = ScopedRegistry.objects.filter(content_type=ct, object_id=project.id).first()

    username = project_registry_username(project.id)

    if scoped and scoped.password and scoped.username == username:
        # Existing per-project credential — refresh the htpasswd line
        # only if the user vanished (e.g. auth dir restored from backup)
        password = scoped.password
        upsert_htpasswd_user(username, password)
    elif _htpasswd_writable():
        # Generate + persist + install into htpasswd
        password = secrets.token_urlsafe(24)
        if scoped:
            scoped.username = username
            scoped.password = password
            scoped.is_internal = True
            scoped.is_active = True
            scoped.save(update_fields=['username', 'password', 'is_internal', 'is_active'])
        else:
            from apps.deployments.services.registry_routing import (
                internal_registry_hosts,
            )
            hosts = list(internal_registry_hosts())
            node_url = master_registry_node_url()
            if node_url and node_url not in hosts:
                hosts.append(node_url)
            scoped = ScopedRegistry.objects.create(
                content_type=ct,
                object_id=project.id,
                username=username,
                password=password,
                allowed_registry_hosts=hosts,
                is_internal=True,
                is_active=True,
            )
        upsert_htpasswd_user(username, password)
    else:
        # Fallback: platform-shared credential (encrypted on the row or
        # resolved from PlatformConfig at use time)
        creds = ScopedRegistry.resolve_registry_credentials(project)
        password = creds.get("password", "")
        username = creds.get("username", "smsly-registry")

    urls = list(internal_registry_hosts())
    node_url = master_registry_node_url()
    if node_url and node_url not in urls:
        urls.append(node_url)

    return {
        "username": username,
        "password": password,
        "per_project": scoped is not None and scoped.username == project_registry_username(project.id),
        "urls": urls,
        "node_url": node_url,
    }


def rotate_project_registry_credentials(project) -> dict:
    """Rotate: new password, update ScopedRegistry + htpasswd."""
    import secrets as _secrets

    from django.contrib.contenttypes.models import ContentType

    from apps.deployments.models.registry_scope import ScopedRegistry

    username = project_registry_username(project.id)
    password = _secrets.token_urlsafe(24)

    if not upsert_htpasswd_user(username, password):
        return {"ok": False, "error": "htpasswd not writable on this install"}

    ct = ContentType.objects.get_for_model(project)
    scoped, _ = ScopedRegistry.objects.update_or_create(
        content_type=ct,
        object_id=project.id,
        defaults={
            "username": username,
            "password": password,
            "is_internal": True,
            "is_active": True,
        },
    )
    logger.info("Rotated registry credentials for project %s", project.id)
    return {"ok": True, "username": username, "password": password}
