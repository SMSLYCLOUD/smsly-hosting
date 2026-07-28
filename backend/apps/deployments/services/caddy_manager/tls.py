import contextlib
import datetime
import ipaddress
import json
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")
CADDY_TOKEN_FILE = os.path.join(CADDY_CONFIG_DIR, ".cloudflare_token")
CADDY_TOKEN_CLEAR_FILE = os.path.join(CADDY_CONFIG_DIR, ".cloudflare_token_clear")
CADDY_TOKEN_CACHE = os.path.join(CADDY_CONFIG_DIR, ".cloudflare_token_cache")
CADDY_TOKEN_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


def _generate_selfsigned_cert(cert_path: str, key_path: str, ip_address: str):
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        logger.warning("cryptography not available; skipping self-signed cert")
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, ip_address or "localhost"),
    ])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(int.from_bytes(secrets.token_bytes(8), "big") & 0x7FFFFFFFFFFFFFFF)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip_address))]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
                content_commitment=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    os.chmod(cert_path, 0o644)
    os.chmod(key_path, 0o644)
    logger.info("Generated self-signed cert for IP: %s", ip_address)


def _read_cached_token_payload() -> dict:
    try:
        if not os.path.exists(CADDY_TOKEN_CACHE):
            return {}
        with open(CADDY_TOKEN_CACHE, encoding="utf-8") as handle:
            raw = (handle.read() or "").strip()
    except OSError:
        return {}
    if not raw or not raw.startswith("{"):
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _load_cached_token() -> str:
    token = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if token:
        return token
    payload = _read_cached_token_payload()
    if not payload:
        return ""
    cached_token = (payload.get("token") or "").strip()
    expires_at = payload.get("expires_at")
    if not cached_token or not isinstance(expires_at, (int, float)):
        return ""
    now = time.time()
    if now >= expires_at:
        logger.warning(
            "Cloudflare token cache is stale (expired at %s, %s days old); ignoring.",
            datetime.datetime.fromtimestamp(expires_at, datetime.UTC).isoformat(),
            int((now - expires_at) / 86400),
        )
        with contextlib.suppress(OSError):
            os.remove(CADDY_TOKEN_CACHE)
        return ""
    return cached_token


def clear_cached_token() -> bool:
    try:
        if os.path.exists(CADDY_TOKEN_CACHE):
            os.remove(CADDY_TOKEN_CACHE)
            logger.info("Cloudflare token cache cleared by operator request")
            return True
    except OSError as exc:
        logger.warning("Failed to clear Cloudflare token cache: %s", exc)
    return False
