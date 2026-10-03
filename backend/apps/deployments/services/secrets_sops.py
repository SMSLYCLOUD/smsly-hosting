"""SOPS-age file secrets — the default secrets backend (Infisical is opt-in).

Why: the Infisical container idles at ~600MB RSS with a 1.5G cap and its
service token was never provisioned anywhere by default, so vault
injection silently never ran while the daemon ate RAM. SOPS+age does the
job the platform actually needs — an encrypted, versioned, off-site copy
of every service's secrets with zero always-on processes:

* Bundles live at ``/app/backups/secrets/<service-id>.enc.yaml`` (the
  host ``backups_data`` volume — persisted, and swept up by server
  backups automatically) plus ``.sops.yaml`` so an operator's local
  ``sops`` CLI can read/edit them with the platform age key.
* ``EnvironmentVariable`` rows (Fernet at rest) stay the runtime source
  of truth — SOPS replaces the vault *sync* layer, not DB storage.
* ``verify_service_bundle`` decrypts and compares key-by-key, reporting
  only key NAMES (never values) on mismatch.

Security rules: values never hit logs (only counts/key names on
mismatch); temp plaintext files are 0600 and removed in finally;
subprocess via argv (no shell); hard timeouts; missing binaries fail
loud with install guidance instead of silent fallback.
"""

import hashlib
import hmac
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

BUNDLE_DIR = "/app/backups/secrets"
SOPS_RULE_FILE = os.path.join(BUNDLE_DIR, ".sops.yaml")
SOPS_TIMEOUT = 120


class SopsError(RuntimeError):
    """User-facing SOPS failure (message contains no secret material)."""


def _which_or_raise(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise SopsError(
            f"{binary} binary missing in backend image — rebuild backend "
            "image (Dockerfile installs age + sops)."
        )
    return path


def _run(args: list[str], input_bytes: bytes | None = None, extra_env: dict | None = None) -> bytes:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            args, input=input_bytes, capture_output=True, timeout=SOPS_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise SopsError(f"{args[0]} binary missing: {exc}")
    except subprocess.TimeoutExpired as exc:
        raise SopsError(f"{args[0]} timed out after {SOPS_TIMEOUT}s")
    if proc.returncode != 0:
        raise SopsError(f"{args[0]} failed (exit {proc.returncode})")
    return proc.stdout or b""


def ensure_age_keypair() -> tuple[str, str]:
    """Return (public_recipient, private_key), creating once if missing.

    Stored on PlatformConfig (private Fernet-encrypted at rest). The
    private key NEVER leaves this function except into the DB field or
    the SOPS_AGE_KEY subprocess env — never logs, never responses.
    """
    from apps.deployments.models.core import PlatformConfig

    cfg = PlatformConfig.load()
    public = str(getattr(cfg, "secrets_age_public_key", "") or "").strip()
    try:
        private = str(cfg.secrets_age_private_key or "").strip()
    except Exception:
        private = ""
    if public.startswith("age1") and private:
        return public, private

    age_keygen = _which_or_raise("age-keygen")
    # NOTE: age-keygen rejects `-o /dev/stdout` ("file exists"); bare
    # invocation prints the public line + secret line to stdout.
    out = _run([age_keygen]).decode()
    new_private, new_public = "", ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Public key:"):
            new_public = line.split(":", 1)[1].strip().split()[0]
        elif line.startswith("age1") and not new_public:
            new_public = line.split()[0]
        elif line.startswith("AGE-SECRET-KEY-") and not new_private:
            new_private = line.split()[0]
    if not (new_public.startswith("age1") and new_private.startswith("AGE-SECRET-KEY-")):
        raise SopsError("age-keygen output unparseable — refusing to store half a keypair")
    cfg.secrets_age_public_key = new_public
    cfg.secrets_age_private_key = new_private
    cfg.save(update_fields=["secrets_age_public_key", "secrets_age_private_key"])
    logger.info("SOPS age keypair created (public %s...)", new_public[:12])
    return new_public, new_private


def _ensure_rule_file(public_recipient: str) -> None:
    """Write .sops.yaml creation rule so operator CLIs Just Work."""
    os.makedirs(BUNDLE_DIR, exist_ok=True)
    content = (
        "creation_rules:\n"
        "  - path_regex: .*\\.enc\\.yaml$\n"
        f"    age: {public_recipient}\n"
    )
    if os.path.exists(SOPS_RULE_FILE):
        try:
            with open(SOPS_RULE_FILE) as fh:
                if public_recipient in fh.read():
                    return
        except OSError:
            pass
    tmp = SOPS_RULE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SOPS_RULE_FILE)


def _bundle_path(service_id: str) -> str:
    safe = "".join(c for c in str(service_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(BUNDLE_DIR, f"{safe}.enc.yaml")


def export_service_bundle(service) -> dict:
    """Encrypt this service's secret rows into its versioned bundle.

    Returns {ok, path, secrets, fingerprint}. Fingerprint is a sha256
    over sorted key names only (no values) for change detection.
    """
    import yaml

    from apps.deployments.models.core import EnvironmentVariable

    public, _ = ensure_age_keypair()
    _ensure_rule_file(public)
    secrets: dict[str, str] = {}
    for ev in EnvironmentVariable.objects.filter(service=service, is_secret=True):
        key = (getattr(ev, "key", "") or "").strip()
        if not key:
            continue
        try:
            value = ev.value or ""
        except Exception:
            continue
        if value:
            secrets[key] = str(value)
    doc = {
        "service": str(getattr(service, "name", "") or ""),
        "service_id": str(getattr(service, "id", "") or ""),
        "exported_by": "smsly-platform",
        "vars": secrets,
    }
    sops = _which_or_raise("sops")
    fd, plain_path = tempfile.mkstemp(prefix="sops-plain-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(doc, fh, default_flow_style=False, allow_unicode=True)
        os.chmod(plain_path, 0o600)
        out_path = _bundle_path(getattr(service, "id", "unknown"))
        _run([sops, "encrypt", "--age", public, "--output", out_path, plain_path])
        os.chmod(out_path, 0o600)
    finally:
        try:
            os.remove(plain_path)
        except OSError:
            pass
    fingerprint = hashlib.sha256(
        ("\n".join(sorted(secrets)) + "\n").encode()
    ).hexdigest()[:16]
    logger.info("SOPS bundle exported for %s: %d secrets", getattr(service, "name", "?"), len(secrets))
    return {"ok": True, "path": out_path, "secrets": len(secrets), "fingerprint": fingerprint}


def _decrypt_bundle(bundle_path: str, private_key: str) -> dict:
    import yaml

    sops = _which_or_raise("sops")
    raw = _run(
        [sops, "decrypt", "--output", "/dev/stdout", bundle_path],
        extra_env={"SOPS_AGE_KEY": private_key},
    )
    data = yaml.safe_load(raw.decode()) or {}
    if not isinstance(data, dict):
        raise SopsError("Bundle decrypted to non-mapping — refusing")
    return data


def verify_service_bundle(service) -> dict:
    """Decrypt the bundle and compare against live DB rows.

    Reports key names only. Returns {ok, secrets, missing, extra,
    mismatched} where mismatched holds names whose values differ.
    """
    from apps.deployments.models.core import EnvironmentVariable

    _, private = ensure_age_keypair()
    path = _bundle_path(getattr(service, "id", ""))
    if not os.path.exists(path):
        return {"ok": False, "reason": "no bundle — export first", "missing": [], "extra": [], "mismatched": []}
    data = _decrypt_bundle(path, private)
    bundled = data.get("vars") or {}
    if not isinstance(bundled, dict):
        return {"ok": False, "reason": "bundle vars not a mapping", "missing": [], "extra": [], "mismatched": []}
    live: dict[str, str] = {}
    for ev in EnvironmentVariable.objects.filter(service=service, is_secret=True):
        key = (getattr(ev, "key", "") or "").strip()
        if not key:
            continue
        try:
            value = ev.value or ""
        except Exception:
            continue
        if value:
            live[key] = str(value)
    missing = sorted(set(live) - set(bundled))
    extra = sorted(set(bundled) - set(live))
    mismatched = sorted(
        k for k in set(live) & set(bundled)
        if not hmac.compare_digest(live[k].encode(), str(bundled[k]).encode())
    )
    ok = not (missing or extra or mismatched)
    return {
        "ok": ok, "secrets": len(live),
        "missing": missing, "extra": extra, "mismatched": mismatched,
    }
