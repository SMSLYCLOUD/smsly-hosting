"""
SPIFFE mTLS Helper Library
===========================
Generic Python library for SPIFFE mTLS integration with any application.
Works with SPIRE agent to automatically load and rotate X.509 SVIDs.

Usage:
    from spiffe_mtls import create_mtls_context, SpiffeMiddleware, create_mtls_client

    # For outgoing requests
    ssl_context = create_mtls_context()
    response = requests.get("https://other-service/path", verify=ssl_context)

    # For FastAPI
    app = FastAPI()
    app.add_middleware(SpiffeMiddleware, trust_domain="platform.local")

    # For Django
    MIDDLEWARE = ["spiffe_mtls.DjangoSpiffeMiddleware"]
"""

from spiffe_mtls.context import create_mtls_context, create_mtls_client
from spiffe_mtls.svid import SvidLoader, SvidRotator
from spiffe_mtls.middleware import SpiffeMiddleware, DjangoSpiffeMiddleware

__version__ = "1.0.0"
__all__ = [
    "create_mtls_context",
    "create_mtls_client",
    "SvidLoader",
    "SvidRotator",
    "SpiffeMiddleware",
    "DjangoSpiffeMiddleware",
]
