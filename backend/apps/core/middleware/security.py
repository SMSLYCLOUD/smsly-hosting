import hmac
import hashlib
import logging
import time
from django.conf import settings
from django.http import JsonResponse
from django.urls import resolve

logger = logging.getLogger(__name__)

class SecurityMiddleware:
    """
    Zero Trust Security Middleware.
    Enforces HMAC V2 Signature Verification for all API requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.secret_key = getattr(settings, 'SECRET_KEY', '')
        # Allow disabling signature check in dev ONLY if explicitly set
        self.enforce_signature = not getattr(settings, 'DEBUG', False)
        
        # Exempt routes (Health checks, Auth callbacks, Admin)
        self.exempt_routes = [
            '/health', 
            '/health/', 
            '/admin/',
            '/static/',
            '/media/',
            # Auth endpoints often need to be public or handle their own flow
            '/accounts/',
            '/api/v1/auth/', 
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

    def _should_verify_signature(self, request):
        """
        Determine if the request requires signature verification.
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
            
        return self.enforce_signature

    def _verify_signature(self, request):
        """
        Verify HMAC V2 Signature.
        Format: METHOD|PATH|TIMESTAMP|BODY_HASH
        Header: X-Gateway-Signature-V2
        """
        signature = request.headers.get('X-Gateway-Signature-V2')
        timestamp = request.headers.get('X-Request-Timestamp')

        if not signature or not timestamp:
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

        # 2. Compute Hash
        method = request.method
        path = request.get_full_path() # Includes query string
        body = request.body
        body_hash = hashlib.sha256(body).hexdigest()

        payload = f"{method}|{path}|{timestamp}|{body_hash}"
        
        # In a real gateway scenario, we might have specific shared secrets.
        # Here we use the Django SECRET_KEY as the shared secret for simplicity 
        # or a specific GATEWAY_SECRET if defined.
        gw_secret = getattr(settings, 'GATEWAY_SECRET', self.secret_key)
        
        expected_signature = hmac.new(
            gw_secret.encode(), 
            payload.encode(), 
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            logger.warning(f"Invalid signature for {request.path}")
            return False

        return True
