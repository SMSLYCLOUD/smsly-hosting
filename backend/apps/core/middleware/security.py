import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)

class SecurityMiddleware:
    """
    Zero Trust Security Middleware.
    Enforces HMAC V2 Signature Verification for all API requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.secret_key = getattr(settings, 'SECRET_KEY', '')

        # Exempt routes (Health checks, Auth callbacks, Admin)
        self.exempt_routes = [
            '/health',
            '/health/',
            '/admin/',
            '/static/',
            '/media/',
            # Auth endpoints often need to be public or handle their own flow
            '/accounts/',
            '/api/v1/accounts/',  # allauth mounted under /api/v1/accounts/ alias
            '/api/v1/auth/',
            '/api/v1/webhooks/',  # Webhooks have their own signature verification
            '/api/v1/system/route-recheck/',  # Public fallback-page recheck hook
            '/api/v1/auth/node-token-exchange',  # Node token exchange handles own auth
            '/api/v1/transfers/register-incoming/',  # Transfer sync verifies node auth in-view
            '/api/v1/services/check-domain/',  # Caddy On-Demand TLS authorization (Public)
            '/api/v1/templates/',  # Public template marketplace (AllowAny)
            '/api/v1/integrations/',  # OAuth authorize/callback flows (public — authenticated elsewhere)
            '/api/v1/oauth/',  # OAuth provider credentials status (public read)
        ]

    def __call__(self, request):
        if self._should_verify_signature(request):
            if not self._verify_signature(request):
                return JsonResponse(
                    {'error': 'Invalid or missing signature'},
                    status=403
                )

        response = self.get_response(request)
        return response

    @property
    def enforce_signature(self):
        """
        Determine if signature verification should be enforced.
        Checks settings dynamically to support test overrides.
        """
        if getattr(settings, 'SMSLY_DISABLE_SIGNATURE_CHECK', False):
            return False
        return not getattr(settings, 'DEBUG', False)

    def _should_verify_signature(self, request):
        """
        Determine if the request requires signature verification.

        HMAC V2 is for INTER-SERVICE authentication (gateway → backend).
        Browser users authenticate via Token auth (Authorization: Token xxx)
        or session auth (Cookie). Those requests skip HMAC verification
        and are instead validated by DRF's authentication classes.
        """
        # Always allow OPTIONS (CORS)
        if request.method == 'OPTIONS':
            return False

        path = request.path

        # Exempt allowlisted paths
        for exempt in self.exempt_routes:
            if path.startswith(exempt):
                return False

        # Only enforce on /api/
        if not path.startswith('/api/'):
            return False

        # Skip if user is already authenticated (e.g. via session/token middleware)
        # SECURITY: But if the request carries HMAC headers, verify them
        # regardless — an inter-service caller should always present a
        # valid signature even if it also happens to have a valid session
        # cookie. This prevents a compromised browser session from being
        # used to forge inter-service requests without the gateway secret.
        if hasattr(request, 'user') and request.user.is_authenticated:
            has_hmac_headers = bool(
                request.headers.get('X-Gateway-Signature-V2')
                and request.headers.get('X-Request-Timestamp')
                and request.headers.get('X-Request-Nonce')
            )
            if has_hmac_headers:
                # HMAC headers present — this is an inter-service request.
                # Fall through to signature verification.
                pass
            else:
                return False

        # DRF test clients using force_authenticate inject these markers.
        # Skip HMAC so application-level auth tests can execute normally.
        if getattr(request, '_force_auth_user', None) is not None:
            return False
        if getattr(request, '_force_auth_token', None) is not None:
            return False

        # Inter-server sync requests carry their own auth (X-SMSLY-Remote-Sync).
        # Skip HMAC verification — DRF token auth will validate them in the view.
        if request.headers.get("X-SMSLY-Remote-Sync") == "1":
            return False

        # Skip only when an API token is present and valid.
        if self._has_valid_token_auth_header(request):
            return False

        # SECURITY: also skip when the HttpOnly auth cookie is present.
        # The login view sets the cookie via ``Set-Cookie`` and the
        # frontend drops the ``Authorization`` header in favour of
        # ``withCredentials: true``; without this branch a logged-in
        # browser user would be forced to provide an HMAC signature
        # on every request, which the platform's CSRF protection
        # explicitly does not require.
        if self._has_valid_token_cookie(request):
            return False

        # Check if a token is passed via query params for downloads
        if self._has_valid_query_token(request):
            return False

        return self.enforce_signature

    def _has_valid_query_token(self, request):
        token_key = request.GET.get('token')
        if not token_key:
            return False

        try:
            from rest_framework.authtoken.models import Token
            return Token.objects.filter(key=token_key).exists()
        except Exception:
            return False

    def _has_valid_token_auth_header(self, request):
        """
        Validate Token/Bearer headers against supported token backends.

        Accepted:
        - Authorization: Token <drf-token>
        - Authorization: Bearer <drf-token>
        - Authorization: Bearer smsly_<api-token>
        """
        auth_header = request.headers.get('Authorization', '').strip()
        if not auth_header:
            return False

        scheme, _, raw_token = auth_header.partition(' ')
        if scheme.lower() not in ('token', 'bearer') or not raw_token:
            return False

        token_key = raw_token.strip()
        if not token_key:
            return False

        try:
            if token_key.startswith("smsly_"):
                from apps.core.models.api_token import APIToken
                token_hash = hashlib.sha256(token_key.encode()).hexdigest()
                return APIToken.objects.filter(
                    token_hash=token_hash,
                    is_active=True,
                ).exists()

            from rest_framework.authtoken.models import Token
            return Token.objects.filter(key=token_key).exists()
        except Exception:
            logger.exception("Token validation failed in SecurityMiddleware")
            return False

    def _has_valid_token_cookie(self, request) -> bool:
        """True if the HttpOnly auth cookie carries a valid DRF token.

        Mirrors ``_has_valid_token_auth_header`` for the cookie transport.
        We deliberately do NOT accept the ``smsly_``-prefixed API-token
        scheme here — those tokens are short-lived operational tokens that
        are never issued to a browser.
        """
        from apps.core.auth_cookies import DEV_COOKIE_NAME, PROD_COOKIE_NAME

        token_key = (
            request.COOKIES.get(PROD_COOKIE_NAME)
            or request.COOKIES.get(DEV_COOKIE_NAME)
        )
        if not token_key:
            return False
        try:
            from rest_framework.authtoken.models import Token
            return Token.objects.filter(key=token_key).exists()
        except Exception:
            logger.exception("Cookie token validation failed in SecurityMiddleware")
            return False

    def _verify_signature(self, request):
        """
        Verify HMAC V2 Signature.
        Format: METHOD|PATH|TIMESTAMP|NONCE|BODY_HASH
        Headers: X-Gateway-Signature-V2, X-Request-Timestamp, X-Request-Nonce

        SECURITY (Batch G): the nonce is mandatory and bound into the
        signed payload. Matches the format expected by the DRF
        ``ZeroTrustHMACAuthentication`` and the inter-service senders
        in ``services/remote_orchestrator.py`` /
        ``services/provisioner.py`` / ``services/transfer_service.py``.
        """
        signature = request.headers.get('X-Gateway-Signature-V2')
        timestamp = request.headers.get('X-Request-Timestamp')
        nonce = request.headers.get('X-Request-Nonce')

        if not signature or not timestamp or not nonce:
            logger.warning(f"Missing signature headers for {request.path}")
            return False

        # 1. Verify timestamp (prevent replay attacks > 5 mins)
        try:
            req_ts = int(timestamp)
            current_ts = int(time.time())
            if abs(current_ts - req_ts) > 300:
                logger.warning(f"Request timestamp expired: {req_ts}")
                return False
        except ValueError:
            return False

        # 2. Nonce replay protection. Each nonce is one-use within
        # the freshness window.
        from django.core.cache import cache
        nonce_key = f"middleware_hmac_nonce:{nonce}"
        if cache.get(nonce_key):
            logger.warning(f"HMAC nonce already used: {nonce[:16]}...")
            return False
        cache.set(nonce_key, "1", timeout=600)

        # 3. Compute Hash
        method = request.method
        path = request.path  # Use path without query string for deterministic signing
        body = request.body
        body_hash = hashlib.sha256(body).hexdigest()

        payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"

        gw_secret = getattr(settings, 'GATEWAY_SECRET', self.secret_key)

        expected_signature = hmac.new(
            gw_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            logger.warning(
                f"Invalid signature for {request.path}. "
                f"Payload: {payload[:100]}... "
                f"Secret-configured: {bool(getattr(settings, 'GATEWAY_SECRET', None))}"
            )
            return False

        return True
