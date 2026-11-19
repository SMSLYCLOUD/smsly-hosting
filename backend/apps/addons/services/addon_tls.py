"""Platform Addon CA, per-addon TLS certificate issuance, and named-volume seeding.

Provides a single internal Platform Addon CA (RSA-2048 X.509) persisted in
``SMSLY_ADDON_CA_DIR`` (defaulting to ``/app/media/.smsly-addon-ca`` when
writable, then ``SMSLY_BUILDS_DIR/addon-ca``, then the system temp dir) and
signs per-addon server certificates seeded into Docker named volumes
(``{container_name}-tls``) via transient helper containers.

Using named Docker volumes seeded by transient containers inside the target
image obeys both:
- ``AGENTS.md`` #23: files land owned by the runtime UID (``postgres:postgres``
  or ``redis:redis``) with ``0600`` private keys.
- ``AGENTS.md`` #24: no host-path bind mounts that could auto-create missing
  directories on the Docker host or fail for non-root SSH users.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import ipaddress
import logging
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from filelock import FileLock

logger = logging.getLogger(__name__)

_CA_CERT_FILENAME = "ca.crt"
_CA_KEY_FILENAME = "ca.key"
_CA_LOCK_FILENAME = ".ca.lock"
_CA_VALIDITY_DAYS = 3650
_LEAF_VALIDITY_DAYS = 825


def _writable_dir(path: str | Path) -> bool:
    try:
        p = Path(path)
        return p.is_dir() and os.access(str(p), os.W_OK | os.X_OK)
    except OSError:
        return False


def get_addon_ca_dir() -> Path:
    """Resolve the writable directory holding the Platform Addon CA."""
    override = (os.getenv("SMSLY_ADDON_CA_DIR") or "").strip()
    if override:
        return Path(override)

    media_root = Path("/app/media")
    if _writable_dir(media_root):
        return media_root / ".smsly-addon-ca"

    builds_override = (os.getenv("SMSLY_BUILDS_DIR") or "").strip()
    if builds_override and _writable_dir(builds_override):
        return Path(builds_override) / "addon-ca"

    default_builds = Path("/opt/smsly-hosting/builds")
    if _writable_dir(default_builds):
        return default_builds / "addon-ca"

    return Path(tempfile.gettempdir()) / "smsly-addon-ca"


def _atomic_write_bytes(path: Path, data: bytes, mode: int) -> None:
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        with contextlib.suppress(OSError):
            os.chmod(path, mode)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _load_valid_ca(cert_path: Path, key_path: Path) -> tuple[bytes, bytes] | None:
    if not (cert_path.is_file() and key_path.is_file()):
        return None
    try:
        cert_pem = cert_path.read_bytes()
        key_pem = key_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        key = serialization.load_pem_private_key(key_pem, password=None)
        if cert.not_valid_after_utc <= datetime.datetime.now(datetime.timezone.utc):
            return None
        if not isinstance(key, rsa.RSAPrivateKey):
            # Support other valid private key types if present, just ensure it parses.
            _ = key.public_key()
        with contextlib.suppress(OSError):
            os.chmod(key_path, 0o600)
            os.chmod(cert_path, 0o644)
        return cert_pem, key_pem
    except Exception as exc:
        logger.warning("Existing addon CA at %s unreadable/invalid (%s); regenerating", cert_path, exc)
        return None


def ensure_addon_ca() -> tuple[bytes, bytes]:
    """Load or atomically generate the Platform Addon CA ``(ca_cert_pem, ca_key_pem)``."""
    ca_dir = get_addon_ca_dir()
    ca_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(ca_dir, 0o755)

    cert_path = ca_dir / _CA_CERT_FILENAME
    key_path = ca_dir / _CA_KEY_FILENAME

    existing = _load_valid_ca(cert_path, key_path)
    if existing is not None:
        return existing

    lock = FileLock(str(ca_dir / _CA_LOCK_FILENAME), timeout=30)
    with lock:
        existing = _load_valid_ca(cert_path, key_path)
        if existing is not None:
            return existing

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SMSLY Hosting"),
            x509.NameAttribute(NameOID.COMMON_NAME, "SMSLY Platform Addon CA"),
        ])
        ski = x509.SubjectKeyIdentifier.from_public_key(private_key.public_key())
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=_CA_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(ski, critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
            .sign(private_key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _atomic_write_bytes(key_path, key_pem, 0o600)
        _atomic_write_bytes(cert_path, cert_pem, 0o644)
        logger.info("Generated Platform Addon CA at %s", ca_dir)
        return cert_pem, key_pem


def get_addon_ca_cert_path() -> str | None:
    """Return local path to ``ca.crt`` (ensuring the CA exists first)."""
    try:
        ensure_addon_ca()
        cert_path = get_addon_ca_dir() / _CA_CERT_FILENAME
        return str(cert_path) if cert_path.is_file() else None
    except Exception as exc:
        logger.warning("Failed to resolve Platform Addon CA path: %s", exc)
        return None


def _build_san_entries(
    common_name: str,
    dns_names: list[str] | None = None,
    ip_addrs: list[str] | None = None,
) -> list[x509.GeneralName]:
    entries: list[x509.GeneralName] = []
    seen_dns: set[str] = set()
    seen_ips: set[str] = set()

    raw_dns = [common_name, *(dns_names or []), "localhost"]
    for item in raw_dns:
        name = str(item or "").strip()
        if not name:
            continue
        # If an IP literal was accidentally passed in dns_names, route it to IPAddress SAN.
        try:
            ip_obj = ipaddress.ip_address(name)
            ip_str = str(ip_obj)
            if ip_str not in seen_ips:
                seen_ips.add(ip_str)
                entries.append(x509.IPAddress(ip_obj))
            continue
        except ValueError:
            pass
        key = name.lower()
        if key not in seen_dns:
            seen_dns.add(key)
            entries.append(x509.DNSName(name))

    raw_ips = ["127.0.0.1", *(ip_addrs or [])]
    for item in raw_ips:
        ip_raw = str(item or "").strip()
        if not ip_raw:
            continue
        try:
            ip_obj = ipaddress.ip_address(ip_raw)
        except ValueError:
            logger.debug("Skipping invalid SAN IP address %r", ip_raw)
            continue
        ip_str = str(ip_obj)
        if ip_str not in seen_ips:
            seen_ips.add(ip_str)
            entries.append(x509.IPAddress(ip_obj))

    return entries


def issue_addon_cert(
    common_name: str,
    dns_names: list[str] | None = None,
    ip_addrs: list[str] | None = None,
    days: int = _LEAF_VALIDITY_DAYS,
) -> tuple[bytes, bytes, bytes]:
    """Sign a server certificate with the Platform Addon CA.

    Returns ``(ca_cert_pem, server_cert_pem, server_key_pem)``.
    """
    ca_cert_pem, ca_key_pem = ensure_addon_ca()
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
    ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)

    cn_raw = str(common_name or "smsly-addon").strip() or "smsly-addon"
    cn_value = cn_raw[:64]

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SMSLY Hosting"),
        x509.NameAttribute(NameOID.COMMON_NAME, cn_value),
    ])

    san_entries = _build_san_entries(cn_raw, dns_names=dns_names, ip_addrs=ip_addrs)
    ski = x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key())

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(ski, critical=False)
    )
    with contextlib.suppress(x509.ExtensionNotFound):
        ca_ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )

    leaf_cert = builder.sign(ca_key, hashes.SHA256())
    leaf_cert_pem = leaf_cert.public_bytes(serialization.Encoding.PEM)
    leaf_key_pem = leaf_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return ca_cert_pem, leaf_cert_pem, leaf_key_pem


def ensure_tls_volume(
    volume_name: str,
    common_name: str,
    dns_names: list[str] | None = None,
    *,
    image: str = "pgvector/pgvector:pg16",
    owner: str = "postgres",
    target_dir: str = "/certs",
    ip_addrs: list[str] | None = None,
    ssh=None,
    timeout: int = 120,
) -> str:
    """Create and seed a named Docker volume with TLS certs owned by ``owner``.

    Secrets pass solely through a temporary ``--env-file`` (deleted in
    ``finally``) so private keys never appear in ``ps`` or container Cmd.
    """
    ca_pem, srv_pem, key_pem = issue_addon_cert(
        common_name, dns_names=dns_names, ip_addrs=ip_addrs
    )
    env_payload = (
        f"CA_CRT={base64.b64encode(ca_pem).decode('ascii')}\n"
        f"SRV_CRT={base64.b64encode(srv_pem).decode('ascii')}\n"
        f"SRV_KEY={base64.b64encode(key_pem).decode('ascii')}\n"
    )

    q_dir = shlex.quote(target_dir)
    q_owner = shlex.quote(f"{owner}:{owner}")
    seed_script = (
        "umask 077 && "
        f"mkdir -p {q_dir} && "
        f"printf '%s' \"$CA_CRT\" | base64 -d > {q_dir}/ca.crt && "
        f"printf '%s' \"$SRV_CRT\" | base64 -d > {q_dir}/server.crt && "
        f"printf '%s' \"$SRV_KEY\" | base64 -d > {q_dir}/server.key && "
        f"(chown -R {q_owner} {q_dir} 2>/dev/null || "
        f"chown {q_owner} {q_dir}/ca.crt {q_dir}/server.crt {q_dir}/server.key 2>/dev/null || true) && "
        f"chmod 0750 {q_dir} && "
        f"chmod 0644 {q_dir}/ca.crt {q_dir}/server.crt && "
        f"chmod 0600 {q_dir}/server.key"
    )

    fd, local_env = tempfile.mkstemp(prefix="smsly-tls-env-", suffix=".env", text=True)
    remote_env = f"/tmp/smsly-tls-env-{uuid.uuid4().hex}.env" if ssh is not None else None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(env_payload)
        with contextlib.suppress(OSError):
            os.chmod(local_env, 0o600)

        if ssh is not None:
            ssh.exec_command(
                f"docker volume create {shlex.quote(volume_name)}",
                timeout=30,
                raise_on_error=False,
            )
            ssh.upload_file(local_env, remote_env)
            remote_cmd = " ".join(
                shlex.quote(p)
                for p in [
                    "docker", "run", "--rm",
                    "-v", f"{volume_name}:{target_dir}",
                    "--env-file", str(remote_env),
                    image,
                    "sh", "-c", seed_script,
                ]
            )
            out, err, code = ssh.exec_command(remote_cmd, timeout=timeout, raise_on_error=False)
            if code != 0:
                raise RuntimeError(
                    f"Remote TLS volume seed failed for {volume_name}: {(err or out or '').strip()[:300]}"
                )
        else:
            subprocess.run(
                ["docker", "volume", "create", volume_name],
                capture_output=True, text=True, check=False, timeout=30,
            )
            subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{volume_name}:{target_dir}",
                    "--env-file", local_env,
                    image,
                    "sh", "-c", seed_script,
                ],
                capture_output=True, text=True, check=True, timeout=timeout,
            )
    finally:
        with contextlib.suppress(OSError):
            os.remove(local_env)
        if ssh is not None and remote_env:
            with contextlib.suppress(Exception):
                ssh.exec_command(f"rm -f {shlex.quote(remote_env)}", timeout=15, raise_on_error=False)

    return volume_name


def remove_tls_volume(volume_name: str, ssh=None, timeout: int = 60) -> None:
    """Best-effort removal of a named TLS volume."""
    if not volume_name:
        return
    try:
        if ssh is not None:
            ssh.exec_command(
                f"docker volume rm {shlex.quote(volume_name)} 2>/dev/null",
                timeout=timeout,
                raise_on_error=False,
            )
        else:
            subprocess.run(
                ["docker", "volume", "rm", volume_name],
                capture_output=True, text=True, check=False, timeout=timeout,
            )
    except Exception as exc:
        logger.debug("TLS volume removal skipped for %s: %s", volume_name, exc)


def ensure_pg_ssl_url(url: str, sslmode: str = "require") -> str:
    """Ensure a PostgreSQL connection URL includes ``sslmode=<sslmode>`` (preserving stronger modes)."""
    raw = str(url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("postgres", "postgresql"):
        return raw
    params = parse_qsl(parsed.query, keep_blank_values=True)
    strong_modes = {"require", "verify-ca", "verify-full"}
    updated: list[tuple[str, str]] = []
    has_sslmode = False
    for key, val in params:
        if key == "sslmode":
            has_sslmode = True
            if val.lower() in strong_modes:
                updated.append((key, val))
            else:
                updated.append((key, sslmode))
        else:
            updated.append((key, val))
    if not has_sslmode:
        updated.append(("sslmode", sslmode))
    return urlunparse(parsed._replace(query=urlencode(updated)))


def ensure_redis_tls_url(url: str, ssl_cert_reqs: str = "none") -> str:
    """Upgrade a Redis URL to ``rediss://`` and ensure ``ssl_cert_reqs`` is present."""
    raw = str(url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("redis", "rediss"):
        return raw
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(k == "ssl_cert_reqs" for k, _ in params):
        params.append(("ssl_cert_reqs", ssl_cert_reqs))
    return urlunparse(parsed._replace(scheme="rediss", query=urlencode(params)))
