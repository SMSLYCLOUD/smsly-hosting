import logging
import time
import uuid

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def _edge_secret() -> str:
    try:
        from django.conf import settings
        raw = str(getattr(settings, "EDGE_JWT_SECRET", "") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        import os
        raw = str(os.environ.get("EDGE_JWT_SECRET", "") or "").strip()
    if not raw:
        raise ImproperlyConfigured(
            "EDGE_JWT_SECRET is not set. "
            "Run: python scripts/generate_env_secrets.py --env .env"
        )
    return raw


class EdgeTokenService:
    @staticmethod
    def mint(user, scope: str = "service", ttl: int = 900) -> str:
        import jwt

        now = int(time.time())
        payload = {
            "sub": str(getattr(user, "id", "")),
            "scope": str(scope or "service"),
            "iat": now,
            "exp": now + max(60, int(ttl or 900)),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, _edge_secret(), algorithm="HS256")

    @staticmethod
    def verify(token: str):
        import jwt

        raw = str(token or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if not raw:
            return None
        try:
            payload = jwt.decode(raw, _edge_secret(), algorithms=["HS256"])
        except Exception as exc:
            logger.debug("Edge JWT verify failed: %s", exc)
            return None
        jti = str(payload.get("jti") or "")
        if not jti:
            return None
        try:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.filter(id=payload.get("sub"), is_active=True).first()
        except Exception:
            return None
        if user is None:
            return None
        return {"user": user, "scope": str(payload.get("scope") or "service"), "jti": jti}
