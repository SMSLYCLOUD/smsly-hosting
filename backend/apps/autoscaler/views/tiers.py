"""Infra (systemd/napd) tier API — sleepable host tiers on the autoscaler page."""
import logging
import re

from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_TIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class InfraTiersViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def _require_staff(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"error": "Admin privileges required."}, status=403)
        return None

    def list(self, request):
        denied = self._require_staff(request)
        if denied is not None:
            return denied
        try:
            from apps.deployments.services.tiers import napd_available, napd_status
            available = napd_available()
            tiers = napd_status() if available else None
        except Exception as exc:
            logger.debug("Infra tiers status failed: %s", exc)
            available, tiers = False, None
        return Response({
            "napd_available": bool(available),
            "tiers": tiers or {},
        })

    @action(detail=False, methods=["post"])
    def wake(self, request):
        denied = self._require_staff(request)
        if denied is not None:
            return denied
        name = str(request.data.get("tier", "") or "").strip().lower()
        if not _TIER_RE.match(name):
            return Response({"ok": False, "error": "Invalid tier name."}, status=400)
        try:
            from apps.deployments.services.tiers import ensure_tier_awake
            ok = bool(ensure_tier_awake(name, timeout=120))
        except Exception as exc:
            logger.debug("Infra tier wake failed for %s: %s", name, exc)
            ok = False
        return Response({"ok": ok, "tier": name})

    @action(detail=False, methods=["post"])
    def sleep(self, request):
        denied = self._require_staff(request)
        if denied is not None:
            return denied
        name = str(request.data.get("tier", "") or "").strip().lower()
        if not _TIER_RE.match(name):
            return Response({"ok": False, "error": "Invalid tier name."}, status=400)
        try:
            from apps.deployments.services.tiers import sleep_tier
            ok = bool(sleep_tier(name))
        except Exception as exc:
            logger.debug("Infra tier sleep failed for %s: %s", name, exc)
            ok = False
        # 409 (not 500): napd refusing (e.g. non-autosleepable tier) is a
        # guard decision, not a server error — the UI surfaces it as-is.
        return Response({"ok": ok, "tier": name}, status=200 if ok else 409)
