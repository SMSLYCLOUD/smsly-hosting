import logging
from rest_framework import permissions, authentication, exceptions, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework.generics import GenericAPIView
from rest_framework import status
from django.conf import settings
import hmac
import time
import hashlib

logger = logging.getLogger(__name__)

class EmptySerializer(serializers.Serializer):
    pass

class SessionTokenView(GenericAPIView):
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['post', 'options', 'head']

    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        Token.objects.filter(user=user).delete()
        new_token = Token.objects.create(user=user)
        return Response({'token': new_token.key})

class ZeroTrustHMACAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        signature = request.headers.get("X-Gateway-Signature-V2", "")
        timestamp = request.headers.get("X-Request-Timestamp", "")
        nonce = request.headers.get("X-Request-Nonce", "")
        if not signature or not timestamp or not nonce:
            return None
        try:
            req_ts = int(timestamp)
            if abs(int(time.time()) - req_ts) > 60:
                raise authentication.AuthenticationFailed("Timestamp expired")
        except ValueError:
            raise authentication.AuthenticationFailed("Invalid timestamp")
        from django.core.cache import cache
        nonce_key = f"hmac_nonce:{nonce}"
        if cache.get(nonce_key):
            raise authentication.AuthenticationFailed("Nonce already used")
        cache.set(nonce_key, "1", timeout=120)
        gw_secret = getattr(settings, "GATEWAY_SECRET", settings.SECRET_KEY)
        method = request.method
        path = request.get_full_path()
        try:
            body = request.body
        except Exception:
            body = b""
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"
        expected = hmac.new(gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise authentication.AuthenticationFailed("Invalid HMAC signature")
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            raise authentication.AuthenticationFailed("No admin user available")
        return (admin, None)

class CaddySecretOrAdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        expected = self._get_expected_secret()
        if expected:
            provided = request.query_params.get("secret", "")
            if provided and hmac.compare_digest(provided, expected):
                return True
            header_provided = request.headers.get("X-Caddy-Secret", "")
            if header_provided and hmac.compare_digest(header_provided, expected):
                return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and (
            getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)
        ):
            return True
        if not expected:
            return True
        return False

    @staticmethod
    def _get_expected_secret():
        try:
            from .models_core import PlatformConfig
            cfg = PlatformConfig.load()
            db_secret = str(getattr(cfg, 'caddy_ask_secret', '') or '').strip()
            if db_secret:
                return db_secret
        except Exception:
            pass
        return str(getattr(settings, "CADDY_ASK_SECRET", "") or "")
