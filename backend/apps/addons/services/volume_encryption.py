"""Per-tenant volume encryption with HKDF+Fernet key wrapping and capability preflight.

Manages wrapped per-volume Data Encryption Keys (DEKs) in
``Addon.provider_metadata['volume_encryption']`` using a domain-separated
Key-Encryption Key (KEK) derived from ``SMSLY_VOLUME_KEK`` or
``FIELD_ENCRYPTION_KEY`` via HKDF-SHA256.

Supports three execution backends when available:
- ``daemon``: Host volume-crypto HTTP helper (e.g. ``SMSLY_VOLUME_DAEMON_URL``).
- ``fscrypt``: Linux kernel ext4/f2fs filesystem encryption via ``scripts/smsly-volume-crypt.sh``.
- ``luks``: LUKS2 loopback encrypted image + ext4 via ``scripts/smsly-volume-crypt.sh``.

Policy semantics (``SMSLY_VOLUME_ENCRYPTION_POLICY``, default ``"preferred"``):
- ``disabled``: Do not encrypt new volumes (still fails closed if an existing
  volume was already marked ``encrypted=True`` and cannot be unlocked).
- ``preferred``: Encrypt with strongest capable backend; if none is available
  (e.g. unprivileged container without host daemon, Windows/WSL2 test host),
  record explicit ``status="fallback_unencrypted"`` + ``reason`` and allow
  standard Docker volume provisioning.
- ``required``: Fail closed by raising :class:`VolumeEncryptionError` if no
  encrypted backend is available or provisioning/unlock fails.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_HKDF_SALT = b"smsly-tenant-volume-kek-v1"
_HKDF_INFO = b"addon-volume-dek-wrap"
_DEFAULT_KEYSTORE_DIR = "/var/lib/smsly/volume-keys"
_DEFAULT_BASE_DIR = "/var/lib/smsly/encrypted-volumes"
_VALID_POLICIES = ("disabled", "preferred", "required")
_VALID_BACKENDS = ("auto", "daemon", "fscrypt", "luks")


class VolumeEncryptionError(RuntimeError):
    """Raised when per-tenant volume encryption fails or is required but unavailable."""


_VOLUME_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_volume_name(volume_name: str) -> str:
    """Validate Docker/host volume identifiers against path traversal and shell metacharacters."""
    if not isinstance(volume_name, str) or not volume_name or ".." in volume_name or not _VOLUME_NAME_RE.fullmatch(volume_name):
        raise VolumeEncryptionError(f"Invalid volume name: {volume_name!r}")
    return volume_name


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def get_encryption_policy() -> str:
    raw = (os.getenv("SMSLY_VOLUME_ENCRYPTION_POLICY") or "preferred").strip().lower()
    if raw not in _VALID_POLICIES:
        logger.warning("Unknown SMSLY_VOLUME_ENCRYPTION_POLICY=%r; defaulting to 'preferred'", raw)
        return "preferred"
    return raw


def get_preferred_backend() -> str:
    raw = (os.getenv("SMSLY_VOLUME_ENCRYPTION_BACKEND") or "auto").strip().lower()
    if raw not in _VALID_BACKENDS:
        logger.warning("Unknown SMSLY_VOLUME_ENCRYPTION_BACKEND=%r; defaulting to 'auto'", raw)
        return "auto"
    return raw


def _resolve_master_key_material() -> bytes | None:
    override = (os.getenv("SMSLY_VOLUME_KEK") or "").strip()
    if override:
        return override.encode("utf-8")

    raw_fek: Any = None
    try:
        from django.conf import settings
        raw_fek = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    except Exception:
        raw_fek = None

    if not raw_fek:
        raw_fek = (os.getenv("FIELD_ENCRYPTION_KEY") or "").strip()

    if isinstance(raw_fek, (list, tuple)):
        raw_fek = raw_fek[0] if raw_fek else None
    if isinstance(raw_fek, bytes):
        return raw_fek if raw_fek else None
    if isinstance(raw_fek, str) and raw_fek.strip():
        return raw_fek.strip().encode("utf-8")
    return None


def derive_volume_kek() -> bytes:
    """Derive a domain-separated 32-byte URL-safe base64 Fernet KEK via HKDF-SHA256."""
    master = _resolve_master_key_material()
    if not master:
        raise VolumeEncryptionError(
            "Neither SMSLY_VOLUME_KEK nor FIELD_ENCRYPTION_KEY is configured for volume key wrapping"
        )
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    derived = hkdf.derive(master)
    return base64.urlsafe_b64encode(derived)


def generate_wrapped_dek() -> tuple[bytes, str, str]:
    """Generate a 256-bit DEK and wrap it with the HKDF-derived Fernet KEK.

    Returns ``(raw_dek_bytes, wrapped_dek_str, key_id)``.
    """
    kek = derive_volume_kek()
    raw_dek = os.urandom(32)
    key_id = hashlib.sha256(raw_dek).hexdigest()[:12]
    wrapped_dek = Fernet(kek).encrypt(raw_dek).decode("ascii")
    return raw_dek, wrapped_dek, key_id


def unwrap_dek(wrapped_dek: str) -> bytes:
    """Unwrap a stored Fernet-encrypted DEK back to raw 32-byte key material."""
    if not wrapped_dek:
        raise VolumeEncryptionError("Missing wrapped_dek for encrypted volume")
    kek = derive_volume_kek()
    try:
        return Fernet(kek).decrypt(wrapped_dek.encode("ascii"))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise VolumeEncryptionError(f"Failed to unwrap volume DEK: {exc}") from exc


def _crypt_script_path() -> Path:
    override = (os.getenv("SMSLY_VOLUME_CRYPT_SCRIPT") or "").strip()
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[4]
    candidate = repo_root / "scripts" / "smsly-volume-crypt.sh"
    if candidate.is_file():
        return candidate
    return Path("/opt/smsly-hosting/scripts/smsly-volume-crypt.sh")


def _daemon_url() -> str:
    explicit = (os.getenv("SMSLY_VOLUME_DAEMON_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    if (os.getenv("SMSLY_PROBE_NAPD_VOLUMES") or "").strip().lower() in ("1", "true", "yes"):
        return (os.getenv("NAPD_BASE_URL") or "http://host.docker.internal:8972").strip().rstrip("/")
    return ""


def _daemon_headers() -> dict[str, str]:
    secret = (
        os.getenv("SMSLY_VOLUME_DAEMON_SECRET")
        or os.getenv("NAPD_SHARED_SECRET")
        or ""
    ).strip()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Napd-Secret"] = secret
    return headers


def _daemon_request(path: str, payload: dict | None = None, *, method: str = "POST", timeout: int = 30) -> tuple[int, dict]:
    base = _daemon_url()
    if not base:
        raise VolumeEncryptionError("Volume daemon URL not configured")
    url = f"{base}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_daemon_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
        try:
            parsed = json.loads(body) if body.strip() else {"error": body}
        except Exception:
            parsed = {"error": body}
        return exc.code, parsed
    except Exception as exc:
        raise VolumeEncryptionError(f"Volume daemon request failed ({path}): {exc}") from exc


def _check_daemon_capable() -> tuple[bool, str]:
    base = _daemon_url()
    if not base:
        return False, "volume daemon URL not configured"
    try:
        code, body = _daemon_request("/volumes/health", payload=None, method="GET", timeout=3)
        if code == 200 and body.get("ok", True):
            return True, "daemon available"
        return False, f"volume daemon returned HTTP {code}"
    except Exception as exc:
        return False, str(exc)


def _is_linux_root() -> tuple[bool, str]:
    if not sys.platform.startswith("linux"):
        return False, f"unsupported OS {sys.platform} (requires Linux host)"
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() != 0:
        return False, f"unprivileged uid {geteuid()} (requires root/CAP_SYS_ADMIN)"
    return True, ""


def _check_fscrypt_capable(base_dir: str = _DEFAULT_BASE_DIR) -> tuple[bool, str]:
    ok_host, reason = _is_linux_root()
    if not ok_host:
        return False, reason
    if not shutil.which("fscrypt"):
        return False, "fscrypt binary not installed"
    script = _crypt_script_path()
    if not script.is_file():
        return False, f"helper script missing: {script}"
    target = base_dir if os.path.isdir(base_dir) else "/var/lib"
    if not os.path.isdir(target):
        target = "/"
    try:
        proc = subprocess.run(
            ["fscrypt", "status", target],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return True, "fscrypt supported"
        detail = (proc.stderr or proc.stdout or "").strip()[:160]
        return False, f"fscrypt status failed: {detail or 'filesystem encryption not enabled'}"
    except Exception as exc:
        return False, f"fscrypt probe failed: {exc}"


def _check_luks_capable() -> tuple[bool, str]:
    ok_host, reason = _is_linux_root()
    if not ok_host:
        return False, reason
    missing = [tool for tool in ("cryptsetup", "losetup", "mkfs.ext4", "mountpoint") if not shutil.which(tool)]
    if missing:
        return False, f"missing host tools: {', '.join(missing)}"
    if not os.path.exists("/dev/mapper/control"):
        return False, "/dev/mapper/control unavailable"
    if not os.path.exists("/dev/loop-control"):
        return False, "/dev/loop-control unavailable"
    script = _crypt_script_path()
    if not script.is_file():
        return False, f"helper script missing: {script}"
    return True, "luks2 loopback supported"


def preflight(backend: str | None = None) -> dict[str, Any]:
    """Probe available per-tenant volume encryption capabilities without mutating state."""
    target = (backend or get_preferred_backend()).strip().lower()
    if target not in _VALID_BACKENDS:
        target = "auto"

    capabilities: dict[str, dict[str, Any]] = {}
    order = ["daemon", "fscrypt", "luks"] if target == "auto" else [target]
    selected: str | None = None
    reasons: list[str] = []

    for cand in order:
        if cand == "daemon":
            ok, msg = _check_daemon_capable()
        elif cand == "fscrypt":
            ok, msg = _check_fscrypt_capable()
        elif cand == "luks":
            ok, msg = _check_luks_capable()
        else:
            ok, msg = False, f"unknown backend {cand}"
        capabilities[cand] = {"available": ok, "detail": msg}
        if ok and selected is None:
            selected = cand
        elif not ok:
            reasons.append(f"{cand}: {msg}")

    return {
        "available": selected is not None,
        "backend": selected,
        "reason": "" if selected else "; ".join(reasons) or "no encrypted volume backend available",
        "capabilities": capabilities,
    }


def _docker_volume_exists(volume_name: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "volume", "inspect", volume_name],
            capture_output=True, text=True, timeout=15,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _save_addon_volume_metadata(addon: Any, ve_meta: dict[str, Any]) -> None:
    try:
        meta = dict(getattr(addon, "provider_metadata", None) or {})
        meta["volume_encryption"] = ve_meta
        addon.provider_metadata = meta
        if hasattr(addon, "save") and getattr(addon, "pk", None) is not None:
            addon.save(update_fields=["provider_metadata", "updated_at"])
    except Exception as exc:
        logger.debug("Could not persist volume_encryption metadata on addon: %s", exc)


def _run_crypt_script(
    action: str,
    volume_name: str,
    *,
    backend: str = "",
    raw_dek: bytes | None = None,
    wrapped_dek: str = "",
    owner_uid: int | None = None,
    owner_gid: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    validate_volume_name(volume_name)
    script = _crypt_script_path()
    cmd = ["bash", str(script), action, volume_name]
    if backend:
        cmd.extend(["--backend", backend])
    if owner_uid is not None:
        cmd.extend(["--uid", str(owner_uid)])
    if owner_gid is not None:
        cmd.extend(["--gid", str(owner_gid)])

    env = dict(os.environ)
    if wrapped_dek:
        env["SMSLY_WRAPPED_DEK"] = wrapped_dek
    if not env.get("SMSLY_VOLUME_SIZE_MB") and env.get("SMSLY_ADDON_VOLUME_SIZE_MB"):
        env["SMSLY_VOLUME_SIZE_MB"] = env["SMSLY_ADDON_VOLUME_SIZE_MB"]

    stdin_data = (raw_dek.hex() + "\n") if raw_dek is not None else None
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "volume crypt helper failed").strip()[:400]
        raise VolumeEncryptionError(f"smsly-volume-crypt {action} failed for {volume_name}: {detail}")
    out = (proc.stdout or "").strip()
    if out:
        try:
            return json.loads(out.splitlines()[-1])
        except Exception:
            return {"output": out}
    return {"ok": True}


def prepare_volume(
    addon: Any,
    volume_name: str,
    *,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> dict[str, Any]:
    validate_volume_name(volume_name)
    """Ensure a per-tenant data volume is provisioned (encrypted when capable).

    - If the volume was previously marked ``encrypted=True`` in
      ``provider_metadata['volume_encryption']``, runs :func:`verify_or_unlock`
      fail-closed so locked volumes are never overwritten by plaintext mounts.
    - If ``policy == "required"`` and no encrypted backend is available,
      raises :class:`VolumeEncryptionError`.
    - If ``policy == "preferred"`` and no encrypted backend is available,
      records ``status="fallback_unencrypted"`` and returns cleanly so Docker
      provisions a standard named volume.
    """
    policy = get_encryption_policy()
    existing_meta = dict(getattr(addon, "provider_metadata", None) or {})
    existing_ve = dict(existing_meta.get("volume_encryption") or {})

    if existing_ve.get("encrypted") and existing_ve.get("wrapped_dek"):
        return verify_or_unlock(addon, volume_name)

    if policy == "disabled":
        ve_meta = {
            "encrypted": False,
            "backend": None,
            "policy": "disabled",
            "status": "disabled",
            "key_id": "",
            "cipher": "",
            "reason": "Volume encryption disabled by SMSLY_VOLUME_ENCRYPTION_POLICY=disabled",
            "updated_at": _utc_now_iso(),
        }
        _save_addon_volume_metadata(addon, ve_meta)
        return ve_meta

    pf = preflight()
    if not pf["available"]:
        reason = pf["reason"]
        if policy == "required":
            raise VolumeEncryptionError(
                f"Per-tenant volume encryption required for {volume_name} but unavailable: {reason}"
            )
        logger.warning(
            "Per-tenant volume encryption unavailable for %s (%s); falling back to standard Docker volume",
            volume_name,
            reason,
        )
        ve_meta = {
            "encrypted": False,
            "backend": None,
            "policy": policy,
            "status": "fallback_unencrypted",
            "key_id": "",
            "cipher": "",
            "reason": reason,
            "updated_at": _utc_now_iso(),
        }
        _save_addon_volume_metadata(addon, ve_meta)
        return ve_meta

    backend = str(pf["backend"])

    # If a pre-existing unencrypted Docker volume already exists on disk, never
    # shadow/wipe its contents during a normal container recreate.
    if _docker_volume_exists(volume_name):
        reason = f"pre-existing unencrypted volume {volume_name} preserved"
        if policy == "required":
            raise VolumeEncryptionError(
                f"Volume {volume_name} already exists unencrypted while policy=required"
            )
        ve_meta = {
            "encrypted": False,
            "backend": None,
            "policy": policy,
            "status": "preexisting_unencrypted",
            "key_id": "",
            "cipher": "",
            "reason": reason,
            "updated_at": _utc_now_iso(),
        }
        _save_addon_volume_metadata(addon, ve_meta)
        return ve_meta

    raw_dek, wrapped_dek, key_id = generate_wrapped_dek()
    cipher = "aes-256-xts-luks2" if backend == "luks" else "fscrypt-v2-aes-256-xts"
    host_path = ""
    try:
        if backend == "daemon":
            code, body = _daemon_request(
                "/volumes/provision",
                {
                    "volume_name": volume_name,
                    "dek_hex": raw_dek.hex(),
                    "wrapped_dek": wrapped_dek,
                    "key_id": key_id,
                    "owner_uid": owner_uid,
                    "owner_gid": owner_gid,
                },
                timeout=120,
            )
            if code >= 400 or not body.get("ok", True):
                raise VolumeEncryptionError(
                    f"Daemon volume provision failed ({code}): {body.get('error', 'unknown error')}"
                )
            host_path = str(body.get("host_path") or "")
            cipher = str(body.get("cipher") or cipher)
        else:
            res = _run_crypt_script(
                "provision",
                volume_name,
                backend=backend,
                raw_dek=raw_dek,
                wrapped_dek=wrapped_dek,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
                timeout=120,
            )
            host_path = str(res.get("host_path") or "")
            cipher = str(res.get("cipher") or cipher)
    except Exception as exc:
        if policy == "required":
            raise VolumeEncryptionError(str(exc)) from exc
        logger.warning("Encrypted volume provision failed for %s (%s); falling back", volume_name, exc)
        ve_meta = {
            "encrypted": False,
            "backend": backend,
            "policy": policy,
            "status": "fallback_unencrypted",
            "key_id": "",
            "cipher": "",
            "reason": str(exc),
            "updated_at": _utc_now_iso(),
        }
        _save_addon_volume_metadata(addon, ve_meta)
        return ve_meta

    ve_meta = {
        "encrypted": True,
        "backend": backend,
        "policy": policy,
        "status": "encrypted_active",
        "key_id": key_id,
        "wrapped_dek": wrapped_dek,
        "host_path": host_path,
        "cipher": cipher,
        "reason": "",
        "updated_at": _utc_now_iso(),
    }
    _save_addon_volume_metadata(addon, ve_meta)
    return ve_meta


def verify_or_unlock(addon: Any, volume_name: str) -> dict[str, Any]:
    """Verify (and unlock if needed) an already-encrypted tenant volume. Fail-closed."""
    validate_volume_name(volume_name)
    meta = dict(getattr(addon, "provider_metadata", None) or {})
    ve = dict(meta.get("volume_encryption") or {})
    if not ve.get("encrypted"):
        return ve

    wrapped_dek = str(ve.get("wrapped_dek") or "")
    backend = str(ve.get("backend") or "auto")
    raw_dek = unwrap_dek(wrapped_dek)

    if backend == "daemon":
        code, body = _daemon_request(
            "/volumes/unlock",
            {
                "volume_name": volume_name,
                "dek_hex": raw_dek.hex(),
                "wrapped_dek": wrapped_dek,
            },
            timeout=60,
        )
        if code >= 400 or not body.get("ok", True):
            raise VolumeEncryptionError(
                f"Failed to verify/unlock volume {volume_name} via daemon ({code}): {body.get('error', '')}"
            )
    else:
        _run_crypt_script(
            "unlock",
            volume_name,
            backend=backend if backend in ("fscrypt", "luks") else "",
            raw_dek=raw_dek,
            wrapped_dek=wrapped_dek,
            timeout=60,
        )

    ve["status"] = "encrypted_active"
    ve["updated_at"] = _utc_now_iso()
    _save_addon_volume_metadata(addon, ve)
    return ve


def cleanup_volume(addon: Any, volume_name: str) -> bool:
    """Destroy encrypted backing storage and wrapped key artifacts for ``volume_name``."""
    validate_volume_name(volume_name)
    meta = dict(getattr(addon, "provider_metadata", None) or {})
    ve = dict(meta.get("volume_encryption") or {})
    is_encrypted = bool(ve.get("encrypted"))
    backend = str(ve.get("backend") or "")
    key_file_exists = os.path.exists(os.path.join(_DEFAULT_KEYSTORE_DIR, f"{volume_name}.enc"))

    if not is_encrypted and not key_file_exists:
        return True

    try:
        if backend == "daemon":
            code, body = _daemon_request(
                "/volumes/cleanup",
                {"volume_name": volume_name},
                timeout=60,
            )
            if code >= 400 or not body.get("ok", True):
                logger.warning("Daemon cleanup failed for %s: %s", volume_name, body)
                return False
        else:
            script = _crypt_script_path()
            if script.is_file() and sys.platform.startswith("linux"):
                _run_crypt_script("cleanup", volume_name, backend=backend, timeout=60)
            elif key_file_exists:
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(_DEFAULT_KEYSTORE_DIR, f"{volume_name}.enc"))
    except Exception as exc:
        logger.warning("Encrypted volume cleanup failed for %s: %s", volume_name, exc)
        return False

    if ve:
        ve["status"] = "cleaned_up"
        ve["updated_at"] = _utc_now_iso()
        ve.pop("wrapped_dek", None)
        _save_addon_volume_metadata(addon, ve)
    return True


def get_sanitized_status(addon: Any) -> dict[str, Any]:
    """Return API-safe volume encryption status (explicitly stripping ``wrapped_dek``)."""
    meta = dict(getattr(addon, "provider_metadata", None) or {})
    ve = dict(meta.get("volume_encryption") or {})
    return {
        "encrypted": bool(ve.get("encrypted", False)),
        "backend": ve.get("backend"),
        "policy": str(ve.get("policy") or get_encryption_policy()),
        "status": str(ve.get("status") or "untracked"),
        "key_id": str(ve.get("key_id") or ""),
        "cipher": str(ve.get("cipher") or ""),
        "reason": str(ve.get("reason") or ""),
        "updated_at": str(ve.get("updated_at") or ""),
    }
