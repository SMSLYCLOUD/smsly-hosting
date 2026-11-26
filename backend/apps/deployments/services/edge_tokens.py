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


def _normalize_host(raw: str | None) -> str:
    """Normalize a Host / X-Forwarded-Host header value by stripping port and trailing dot."""
    val = str(raw or "").strip()
    if not val:
        return ""
    if "," in val:
        val = val.split(",", 1)[0].strip()
    if val.startswith("["):
        end = val.find("]")
        if end != -1:
            val = val[1:end]
    elif ":" in val:
        val = val.split(":", 1)[0]
    return val.strip().rstrip(".").lower()


class EdgeTokenService:
    @staticmethod
    def mint(
        user,
        scope: str = "service",
        ttl: int = 900,
        aud: str | None = None,
        audience: str = "",
    ) -> str:
        import jwt

        now = int(time.time())
        try:
            bounded_ttl = max(60, min(int(ttl if ttl is not None else 900), 86400))
        except (TypeError, ValueError):
            bounded_ttl = 900
        payload = {
            "sub": str(getattr(user, "id", "")),
            "scope": str(scope or "service"),
            "iat": now,
            "exp": now + bounded_ttl,
            "jti": uuid.uuid4().hex,
        }
        norm_aud = _normalize_host(aud if aud is not None else audience)
        if norm_aud:
            payload["aud"] = norm_aud
        return jwt.encode(payload, _edge_secret(), algorithm="HS256")

    @staticmethod
    def verify(token: str, aud: str | None = None, audience: str | None = None):
        import jwt

        raw = str(token or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if not raw or len(raw) > 8192:
            return None
        try:
            payload = jwt.decode(
                raw,
                _edge_secret(),
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except Exception as exc:
            logger.debug("Edge JWT verify failed: %s", exc)
            return None
        jti = str(payload.get("jti") or "")
        if not jti:
            return None
        token_aud = _normalize_host(payload.get("aud"))
        check_aud = aud if aud is not None else audience
        if check_aud is not None:
            if not token_aud:
                if payload.get("scope") != "admin":
                    return None
            else:
                expected_aud = _normalize_host(check_aud)
                if not expected_aud or token_aud != expected_aud:
                    return None
        try:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.filter(id=payload.get("sub"), is_active=True).first()
        except Exception:
            return None
        if user is None:
            return None
        out = {"user": user, "scope": str(payload.get("scope") or "service"), "jti": jti}
        if token_aud:
            out["aud"] = token_aud
        return out
