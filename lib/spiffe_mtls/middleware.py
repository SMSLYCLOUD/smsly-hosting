"""
FastAPI and Django Middleware
=============================
Middleware for verifying incoming mTLS connections using SPIFFE SVIDs.
Supports L7 authorization policies for service-to-service access control.
"""

import os
import ssl
import logging
from typing import Callable, Optional, Set

logger = logging.getLogger(__name__)


class SpiffeMiddleware:
    """
    FastAPI/ASGI middleware for SPIFFE mTLS verification.

    Verifies that incoming connections present a valid X.509 certificate
    signed by the SPIRE CA, and that the SPIFFE ID is in the allowed callers list.

    Optionally enforces L7 authorization policies via a policy_check_fn callback.

    Usage:
        from fastapi import FastAPI
        from spiffe_mtls import SpiffeMiddleware

        app = FastAPI()
        app.add_middleware(
            SpiffeMiddleware,
            trust_domain="platform.local",
            allowed_callers={"service/gateway", "service/backend"},
            policy_check_fn=my_policy_checker,
        )
    """

    def __init__(
        self,
        app,
        trust_domain: Optional[str] = None,
        allowed_callers: Optional[Set[str]] = None,
        exempt_paths: Optional[Set[str]] = None,
        fail_closed: bool = True,
        policy_check_fn: Optional[Callable] = None,
    ):
        self.app = app
        self.trust_domain = trust_domain or os.getenv(
            "SPIFFE_TRUST_DOMAIN", "platform.local"
        )
        self.allowed_callers = allowed_callers or set()
        self.exempt_paths = exempt_paths or {"/health", "/ready", "/metrics", "/"}
        self.fail_closed = fail_closed
        self.policy_check_fn = policy_check_fn

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
                await self._send_error(send, 401, "Missing SPIFFE identity")
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
            await self._send_error(send, 401, "Invalid SPIFFE trust domain")
            return

        # Validate allowed callers (static list)
        if self.allowed_callers:
            caller_path = spiffe_id[len(expected_prefix):]
            if caller_path not in self.allowed_callers:
                logger.warning(
                    "mTLS rejection: caller not allowed (id=%s, allowed=%s)",
                    spiffe_id,
                    self.allowed_callers,
                )
                await self._send_error(send, 401, "Caller not authorized")
                return

        # L7 authorization policy check
        if self.policy_check_fn:
            method = scope.get("method", "GET")
            allowed = self.policy_check_fn(spiffe_id, path, method)
            if not allowed:
                logger.warning(
                    "mTLS rejection: policy denied (id=%s, path=%s, method=%s)",
                    spiffe_id,
                    path,
                    method,
                )
                await self._send_error(send, 403, "Authorization denied by policy")
                return

        # Attach SPIFFE ID to scope for downstream handlers
        scope["spiffe_id"] = spiffe_id
        logger.debug("mTLS: authenticated %s", spiffe_id)
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

    async def _send_error(self, send, status: int, message: str):
        """Send error response."""
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [[b"content-type", b"application/json"]],
        })
        await send({
            "type": "http.response.body",
            "body": f'{{"error": "unauthorized", "message": "{message}"}}'.encode(),
        })


class DjangoSpiffeMiddleware:
    """
    Django middleware for SPIFFE mTLS verification with L7 policy enforcement.

    When SPIFFE_MTLS_POLICY_CHECK enabled, checks MtlsAuthorizationPolicy rules
    for each inbound request. Policies are matched by source SPIFFE ID, path prefix,
    and HTTP method. First matching rule wins (ALLOW or DENY). If no rule matches,
    the request is denied (fail_closed=True) or allowed (fail_closed=False).

    Usage:
        # settings.py
        MIDDLEWARE = [
            "spiffe_mtls.DjangoSpiffeMiddleware",
            # ... other middleware
        ]

        SPIFFE_TRUST_DOMAIN = "ecosystem.local"
        SPIFFE_EXEMPT_PATHS = ["/health/", "/ready/", "/metrics/"]
        SPIFFE_FAIL_CLOSED = True
        SPIFFE_MTLS_POLICY_CHECK = True  # Enable L7 authorization policies
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._trust_domain = None
        self._exempt_paths = None
        self._fail_closed = None
        self._policy_check_enabled = None

    def _init_settings(self):
        """Lazy-load Django settings once."""
        if self._trust_domain is not None:
            return
        from django.conf import settings
        self._trust_domain = getattr(settings, "SPIFFE_TRUST_DOMAIN", "ecosystem.local")
        self._exempt_paths = getattr(settings, "SPIFFE_EXEMPT_PATHS", {"/health/", "/ready/", "/metrics/"})
        self._fail_closed = getattr(settings, "SPIFFE_FAIL_CLOSED", True)
        self._policy_check_enabled = getattr(settings, "SPIFFE_MTLS_POLICY_CHECK", False)

    def __call__(self, request):
        self._init_settings()

        # Skip exempt paths
        if request.path in self._exempt_paths:
            return self.get_response(request)

        # Check for SPIFFE ID in headers
        spiffe_id = (
            request.META.get("HTTP_X_SPIFFE_ID")
            or request.META.get("HTTP_X_FORWARDED_SPIFFE_ID")
        )

        if not spiffe_id:
            if self._fail_closed:
                from django.http import JsonResponse
                return JsonResponse(
                    {"error": "unauthorized", "message": "Missing SPIFFE identity"},
                    status=401,
                )
            else:
                return self.get_response(request)

        # Validate trust domain
        expected_prefix = f"spiffe://{self._trust_domain}/"
        if not spiffe_id.startswith(expected_prefix):
            from django.http import JsonResponse
            return JsonResponse(
                {"error": "unauthorized", "message": "Invalid SPIFFE trust domain"},
                status=401,
            )

        # L7 authorization policy check
        if self._policy_check_enabled:
            from django.http import JsonResponse

            allowed = self._check_policy(spiffe_id, request)
            if not allowed:
                logger.warning(
                    "mTLS policy denied: %s %s from %s",
                    request.method,
                    request.path,
                    spiffe_id,
                )
                return JsonResponse(
                    {"error": "forbidden", "message": "Authorization denied by policy"},
                    status=403,
                )

        # Attach SPIFFE ID to request for downstream handlers
        request.spiffe_id = spiffe_id
        response = self.get_response(request)
        return response

    def _check_policy(self, spiffe_id: str, request) -> bool:
        """
        Check MtlsAuthorizationPolicy rules for this request.

        Returns True if allowed, False if denied.
        """
        try:
            from apps.mtls.models import MtlsAuthorizationPolicy
            from apps.deployments.models import Service

            # Resolve target service from the request
            target_service = self._resolve_target_service(request)
            if not target_service:
                # Can't resolve target — allow (handled by other middleware)
                logger.debug("mTLS policy: no target service resolved, allowing")
                return True

            # Get policies for this target service, ordered by priority
            policies = MtlsAuthorizationPolicy.objects.filter(
                target_service=target_service,
                enabled=True,
            ).order_by("-priority", "id")

            for policy in policies:
                if policy.matches(spiffe_id, request.path, request.method):
                    logger.debug(
                        "mTLS policy matched: %s (action=%s)",
                        policy.name,
                        policy.action,
                    )
                    return policy.action == MtlsAuthorizationPolicy.Action.ALLOW

            # No policy matched — default deny
            logger.debug(
                "mTLS policy: no match for %s -> %s %s, defaulting to deny",
                spiffe_id,
                request.method,
                request.path,
            )
            return False

        except ImportError:
            # MtlsAuthorizationPolicy not installed — skip policy check
            logger.debug("mTLS policy check skipped: MtlsAuthorizationPolicy not installed")
            return True
        except Exception as e:
            logger.error("mTLS policy check error: %s", e)
            # Fail open on errors to avoid breaking the platform
            return True

    def _resolve_target_service(self, request):
        """
        Resolve the target Service from the incoming request.

        Strategy:
        1. Check X-Target-Service header (set by Envoy sidecar or reverse proxy)
        2. Match request Host header against service domains
        3. Return None if unresolvable (allow other middleware to handle)
        """
        from apps.deployments.models import Service

        # Strategy 1: Explicit header
        target_name = request.META.get("HTTP_X_TARGET_SERVICE")
        if target_name:
            try:
                return Service.objects.get(name=target_name)
            except Service.DoesNotExist:
                return None

        # Strategy 2: Domain matching
        host = request.get_host().split(":")[0]  # strip port
        try:
            service = Service.objects.filter(
                public_domain__iexact=host,
                status="RUNNING",
            ).first()
            if service:
                return service
        except Exception:
            pass

        # Strategy 3: Custom domain matching
        try:
            from apps.domains.models import CustomDomain
            domain_obj = CustomDomain.objects.filter(
                domain__iexact=host,
                verified=True,
            ).select_related("service").first()
            if domain_obj:
                return domain_obj.service
        except Exception:
            pass

        return None
