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


def project_image_namespace(service) -> str:
    """Return the registry repository namespace for a service's images.

    Any service in a project builds into ``proj-<first 8 of project
    uuid>`` — the same stable identity as its per-project registry
    credential — so every project gets its own storage namespace
    instead of sharing the global ``smsly/`` one. Same-project
    redeploys reuse the namespace; only ungrouped services keep the
    legacy ``smsly/`` namespace. Pure function: reads
    ``managed_by``/``project_id`` only, no DB access.

    Caveat: the live registry enforces htpasswd auth, which cannot scope
    paths — any valid user can technically pull any repo. The namespace
    gives per-project storage separation, collision-freedom, and
    browsability, and matches the ``proj-*`` credential identity, but
    hard path enforcement needs the token-auth migration
    (``apps/deployments/views/registry_auth.py``).
    """
    try:
        project_id = getattr(service, "project_id", None)
        if project_id:
            return f"proj-{str(project_id).replace('-', '')[:8]}"
    except Exception:
        pass
    return "smsly"


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


_NODE_SYNC_PREFIX = "__node_sync__:"


def _normalize_registry_host(url: str) -> str:
    raw = str(url or "").strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    return raw.split("/", 1)[0].strip().rstrip("/")


def _node_sync_cred_name(registry_host: str, namespace: str = "") -> str:
    base = f"{_NODE_SYNC_PREFIX}{registry_host}|{namespace}"
    if len(base) <= 200:
        return base
    import hashlib
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"{_NODE_SYNC_PREFIX}{registry_host[:160]}|{digest}"


def upsert_synced_pull_credentials(credentials: list[dict], owner=None) -> int:
    """Persist pull credentials synced from master into encrypted RegistryCredential rows.

    Keyed deterministically by (registry_host, namespace) under a
    ``__node_sync__:`` prefix (filtered out of user-facing API lists)
    so worker nodes keep distinct master/project pull auth without
    mutating their own local registry secrets.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.models.registry import RegistryCredential

    if owner is None or not getattr(owner, "pk", None):
        User = get_user_model()
        owner = (
            User.objects.filter(is_superuser=True).order_by("id").first()
            or User.objects.order_by("id").first()
        )
        if owner is None:
            raise ValueError("No local user account available to store synced registry credentials.")

    stored = 0
    for item in credentials:
        if not isinstance(item, dict):
            raise ValueError("Each credential entry must be an object.")
        host = _normalize_registry_host(item.get("registry") or "")
        if not host or len(host) > 255 or any(c.isspace() for c in host):
            raise ValueError("Invalid registry host in credential entry.")
        namespace = str(item.get("namespace") or "").strip()[:128]
        if any(c.isspace() for c in namespace):
            raise ValueError("Invalid namespace in credential entry.")
        username = str(item.get("username") or "").strip()
        password = str(item.get("password") or "")
        if not username or not password:
            continue

        cred_name = _node_sync_cred_name(host, namespace)
        existing = RegistryCredential.objects.filter(name=cred_name).first()
        if existing is not None:
            existing.registry_url = host
            existing.provider = "custom"
            existing.username = username
            existing.password = password
            existing.is_active = True
            existing.save(
                update_fields=["registry_url", "provider", "username", "password", "is_active", "updated_at"]
            )
        else:
            RegistryCredential.objects.create(
                owner=owner,
                name=cred_name,
                provider="custom",
                registry_url=host,
                username=username,
                password=password,
                is_active=True,
            )
        stored += 1
    return stored


def resolve_pull_credentials(image: str, scope_obj=None) -> tuple[str, str, str]:
    """Return ``(registry_host, username, password)`` for pulling *image*.

    Resolution priority:
      1. Synced node pull credentials (``RegistryCredential`` with
         ``__node_sync__:`` prefix) matching ``(img_host, namespace)`` then
         ``(img_host, "")`` — authoritative on worker nodes whose local
         ``.env`` REGISTRY_PASSWORD differs from master's.
      2. Explicit service ``registry_credential`` or ``ScopedRegistry`` chain
         when matching the image's registry host.
      3. Local ``PlatformConfig`` / ``settings`` registry credentials when the
         target host is our cluster/local registry.
    """
    import os
    from django.conf import settings as dj_settings
    from django.db import OperationalError, ProgrammingError

    from apps.deployments.services.registry_routing import (
        _split_ref,
        internal_registry_hosts,
        is_master_registry_ref,
        master_registry_node_url,
    )

    ref = str(image or "").strip()
    img_host, rest = _split_ref(ref)
    if not img_host:
        return "", "", ""
    namespace = rest.split("/", 1)[0] if "/" in rest else ""
    is_worker_node = os.environ.get("MODE", "").strip().lower() == "node"

    # 1. Node-synced pull credentials (per-namespace first, then host default).
    try:
        from apps.deployments.models.registry import RegistryCredential

        candidate_names = (
            [_node_sync_cred_name(img_host, namespace), _node_sync_cred_name(img_host, "")]
            if namespace
            else [_node_sync_cred_name(img_host, "")]
        )
        rows = {
            r.name: r
            for r in RegistryCredential.objects.filter(
                name__in=candidate_names,
                registry_url=img_host,
                is_active=True,
            )
        }
        for cname in candidate_names:
            row = rows.get(cname)
            if row and (row.username or "").strip() and (row.password or ""):
                return img_host, row.username.strip(), row.password
    except (OperationalError, ProgrammingError):
        pass
    except Exception as exc:
        logger.debug("Synced pull credential lookup failed for %s: %s", img_host, exc)

    # Compute master node URL early so Priority 2 can treat internal + master aliases uniformly.
    try:
        master_url = (master_registry_node_url() or "").strip()
    except Exception:
        master_url = ""

    # 2. Explicit Service.registry_credential or ScopedRegistry chain.
    if scope_obj is not None:
        try:
            svc_cred = getattr(scope_obj, "registry_credential", None)
            if svc_cred is not None and getattr(svc_cred, "is_active", False):
                cred_host = _normalize_registry_host(getattr(svc_cred, "registry_url", ""))
                u = str(getattr(svc_cred, "username", "") or "").strip()
                p = str(getattr(svc_cred, "password", "") or "")
                if cred_host == img_host and u and p:
                    return img_host, u, p
        except Exception:
            pass
        try:
            from apps.deployments.models.registry_scope import ScopedRegistry

            project_scope = getattr(scope_obj, "project", None) or scope_obj
            info = ScopedRegistry.resolve_registry_credentials(project_scope) or {}
            scoped_host = _normalize_registry_host(info.get("url") or "")
            u = str(info.get("username") or "").strip()
            p = str(info.get("password") or "")
            try:
                internal_hosts = set(internal_registry_hosts())
            except Exception:
                internal_hosts = set()
            internal_or_master = internal_hosts | ({master_url} if master_url else set())
            if u and p:
                direct_match = scoped_host == img_host and not (is_worker_node and scoped_host in internal_hosts)
                cluster_match = (
                    not is_worker_node
                    and is_master_registry_ref(ref)
                    and scoped_host in internal_or_master
                )
                if direct_match or cluster_match:
                    return img_host, u, p
        except (OperationalError, ProgrammingError):
            pass
        except Exception as exc:
            logger.debug("ScopedRegistry pull lookup failed for %s: %s", img_host, exc)

    # 3. PlatformConfig / settings fallback for internal / port-5000 registries.
    configured_host = _normalize_registry_host(getattr(dj_settings, "CONTAINER_REGISTRY_URL", "") or "")
    is_cluster_registry = (
        is_master_registry_ref(ref)
        or img_host.endswith(":5000")
        or (configured_host and img_host == configured_host)
        or (master_url and img_host == master_url)
    )
    if not is_cluster_registry:
        return "", "", ""

    user = str(getattr(dj_settings, "REGISTRY_USER", "") or "").strip()
    password = str(getattr(dj_settings, "REGISTRY_PASSWORD", "") or "").strip()
    try:
        from apps.deployments.models.core import PlatformConfig

        user = (PlatformConfig.get_config_value("registry_user") or user or "smsly-registry").strip()
        password = (PlatformConfig.get_config_value("registry_password") or password or "").strip()
    except Exception:
        pass
    if user and password:
        return img_host, user, password
    return "", "", ""
