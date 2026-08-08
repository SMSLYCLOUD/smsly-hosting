"""
SSL Context Creation
====================
Creates SSL contexts using SPIFFE SVIDs for mTLS.
"""

import ssl
import tempfile
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def create_mtls_context(
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    bundle_path: Optional[str] = None,
    server_side: bool = False,
) -> ssl.SSLContext:
    """
    Create an SSL context using SPIFFE SVIDs for mTLS.

    Args:
        cert_path: Path to X.509 certificate (SVID). Defaults to SPIFFE_SVID_CERT_PATH env var.
        key_path: Path to private key. Defaults to SPIFFE_SVID_KEY_PATH env var.
        bundle_path: Path to trust bundle (root CA). Defaults to SPIFFE_BUNDLE_PATH env var.
        server_side: If True, create server context (requires client certs). If False, client context.

    Returns:
        ssl.SSLContext configured for mTLS.

    Raises:
        FileNotFoundError: If SVID files are not found and no fallback is configured.
    """
    cert = cert_path or os.getenv("SPIFFE_SVID_CERT_PATH", "/opt/spire/svids/cert.pem")
    key = key_path or os.getenv("SPIFFE_SVID_KEY_PATH", "/opt/spire/svids/key.pem")
    bundle = bundle_path or os.getenv("SPIFFE_BUNDLE_PATH", "/opt/spire/svids/bundle.pem")

    # Verify files exist
    for path, name in [(cert, "cert"), (key, "key"), (bundle, "bundle")]:
        if not Path(path).exists():
            raise FileNotFoundError(
                f"SPIFFE {name} not found at {path}. "
                f"Ensure SPIRE agent is running and SVIDs are available."
            )

    if server_side:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        ctx.load_verify_locations(cafile=bundle)
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        ctx.load_verify_locations(cafile=bundle)
        ctx.check_hostname = False  # SPIFFE IDs don't match DNS hostnames
        ctx.verify_mode = ssl.CERT_REQUIRED

    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    logger.info("mTLS context created (server_side=%s, cert=%s)", server_side, cert)
    return ctx


def create_mtls_client(
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    bundle_path: Optional[str] = None,
):
    """
    Create an httpx.AsyncClient with mTLS configured.

    Returns:
        httpx.AsyncClient with mTLS SSL context.

    Usage:
        client = create_mtls_client()
        response = await client.get("https://other-service/path")
    """
    try:
        import httpx
    except ImportError:
        raise ImportError("httpx is required for create_mtls_client(). Install with: pip install httpx")

    ctx = create_mtls_context(cert_path, key_path, bundle_path, server_side=False)
    return httpx.AsyncClient(verify=ctx)


def create_mtls_requests_session(
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    bundle_path: Optional[str] = None,
):
    """
    Create a requests.Session with mTLS configured.

    Returns:
        requests.Session with mTLS SSL context.

    Usage:
        session = create_mtls_requests_session()
        response = session.get("https://other-service/path")
    """
    try:
        import requests
    except ImportError:
        raise ImportError("requests is required. Install with: pip install requests")

    from requests.adapters import HTTPAdapter

    ctx = create_mtls_context(cert_path, key_path, bundle_path, server_side=False)
    session = requests.Session()
    session.mount('https://', HTTPAdapter(ssl_context=ctx))
    return session
