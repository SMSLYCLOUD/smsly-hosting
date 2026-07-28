"""
FastAPI and Django Middleware
=============================
Middleware for verifying incoming mTLS connections using SPIFFE SVIDs.
"""

import os
import ssl
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)


class SpiffeMiddleware:
    """
    FastAPI/ASGI middleware for SPIFFE mTLS verification.

    Verifies that incoming connections present a valid X.509 certificate
    signed by the SPIRE CA, and that the SPIFFE ID is in the allowed callers list.

    Usage:
        from fastapi import FastAPI
        from spiffe_mtls import SpiffeMiddleware

        app = FastAPI()
        app.add_middleware(
            SpiffeMiddleware,
            trust_domain="platform.local",
            allowed_callers={"service/gateway", "service/backend"},
        )
    """

    def __init__(
        self,
        app,
        trust_domain: Optional[str] = None,
        allowed_callers: Optional[Set[str]] = None,
        exempt_paths: Optional[Set[str]] = None,
        fail_closed: bool = True,
    ):
        self.app = app
        self.trust_domain = trust_domain or os.getenv(
            "SPIFFE_TRUST_DOMAIN", "platform.local"
        )
        self.allowed_callers = allowed_callers or set()
        self.exempt_paths = exempt_paths or {"/health", "/ready", "/metrics", "/"}
        self.fail_closed = fail_closed

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "/")
        if path in self.exempt_paths:
            return await self.app(scope, receive, send)

        # Check for SPIFFE ID in headers (set by Traefik or reverse proxy)
        headers = dict(scope.get("headers", []))
        spiffe_id = None

        # Try multiple header names
        for header_name in [b"x-spiffe-id", b"x-forwarded-spiffe-id"]:
            if header_name in headers:
                spiffe_id = headers[header_name].decode("utf-8")
                break

        # Also check SSL context if available
        if not spiffe_id and "extensions" in scope:
            ssl_context = scope.get("extensions", {}).get("ssl", {})
            if ssl_context:
                # Extract SPIFFE ID from client certificate SAN
                peer_cert = ssl_context.get("peer_cert_DERs", [])
                if peer_cert:
                    spiffe_id = self._extract_spiffe_id_from_cert(peer_cert[0])

        if not spiffe_id:
            if self.fail_closed:
                logger.warning("mTLS rejection: no SPIFFE ID (path=%s)", path)
                await self._send_401(send, "Missing SPIFFE identity")
                return
            else:
                logger.info("mTLS: no SPIFFE ID, failing open (path=%s)", path)
                return await self.app(scope, receive, send)

        # Validate trust domain
        expected_prefix = f"spiffe://{self.trust_domain}/"
        if not spiffe_id.startswith(expected_prefix):
            logger.warning(
                "mTLS rejection: invalid trust domain (id=%s, expected=%s)",
                spiffe_id,
                self.trust_domain,
            )
            await self._send_401(send, "Invalid SPIFFE trust domain")
            return

        # Validate allowed callers
        if self.allowed_callers:
            caller_path = spiffe_id[len(expected_prefix):]
            if caller_path not in self.allowed_callers:
                logger.warning(
                    "mTLS rejection: caller not allowed (id=%s, allowed=%s)",
                    spiffe_id,
                    self.allowed_callers,
                )
                await self._send_401(send, "Caller not authorized")
                return

        # Attach SPIFFE ID to scope for downstream handlers
        scope["spiffe_id"] = spiffe_id
        logger.info("mTLS: authenticated %s", spiffe_id)
        return await self.app(scope, receive, send)

    def _extract_spiffe_id_from_cert(self, cert_der: bytes) -> Optional[str]:
        """Extract SPIFFE ID from X.509 certificate SAN."""
        try:
            from cryptography import x509
            cert = x509.load_der_x509_certificate(cert_der)
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            for uri in san.value.get_values_for_type(x509.UniformResourceIdentifier):
                if uri.startswith("spiffe://"):
                    return uri
        except Exception as e:
            logger.error("Failed to extract SPIFFE ID from cert: %s", e)
        return None

    async def _send_401(self, send, message: str):
        """Send 401 Unauthorized response."""
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [[b"content-type", b"application/json"]],
        })
        await send({
            "type": "http.response.body",
            "body": f'{{"error": "unauthorized", "message": "{message}"}}'.encode(),
        })


class DjangoSpiffeMiddleware:
    """
    Django middleware for SPIFFE mTLS verification.

    Usage:
        # settings.py
        MIDDLEWARE = [
            "spiffe_mtls.DjangoSpiffeMiddleware",
            # ... other middleware
        ]

        SPIFFE_TRUST_DOMAIN = "platform.local"
        SPIFFE_ALLOWED_CALLERS = {"service/gateway", "service/backend"}
        SPIFFE_EXEMPT_PATHS = ["/health/", "/ready/", "/metrics/"]
        SPIFFE_FAIL_CLOSED = True
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._spiffe_middleware = None

    def _get_middleware(self, request):
        """Lazy-initialize SpiffeMiddleware with Django settings."""
        if self._spiffe_middleware is None:
            from django.conf import settings

            self._spiffe_middleware = SpiffeMiddleware(
                app=None,
                trust_domain=getattr(settings, "SPIFFE_TRUST_DOMAIN", "platform.local"),
                allowed_callers=getattr(settings, "SPIFFE_ALLOWED_CALLERS", set()),
                exempt_paths=getattr(settings, "SPIFFE_EXEMPT_PATHS", {"/health/", "/ready/", "/metrics/"}),
                fail_closed=getattr(settings, "SPIFFE_FAIL_CLOSED", True),
            )
        return self._spiffe_middleware

    def __call__(self, request):
        # Initialize middleware lazily
        middleware = self._get_middleware(request)

        # Check exempt paths
        if request.path in middleware.exempt_paths:
            return self.get_response(request)

        # Check for SPIFFE ID in headers
        spiffe_id = request.META.get("HTTP_X_SPIFFE_ID") or request.META.get("HTTP_X_FORWARDED_SPIFFE_ID")

        if not spiffe_id:
            if middleware.fail_closed:
                from django.http import JsonResponse
                return JsonResponse({"error": "unauthorized", "message": "Missing SPIFFE identity"}, status=401)
            else:
                return self.get_response(request)

        # Validate trust domain
        trust_domain = middleware.trust_domain
        expected_prefix = f"spiffe://{trust_domain}/"
        if not spiffe_id.startswith(expected_prefix):
            from django.http import JsonResponse
            return JsonResponse({"error": "unauthorized", "message": "Invalid SPIFFE trust domain"}, status=401)

        # Validate allowed callers
        if middleware.allowed_callers:
            caller_path = spiffe_id[len(expected_prefix):]
            if caller_path not in middleware.allowed_callers:
                from django.http import JsonResponse
                return JsonResponse({"error": "unauthorized", "message": "Caller not authorized"}, status=401)

        # Attach SPIFFE ID to request
        request.spiffe_id = spiffe_id
        response = self.get_response(request)
        return response
