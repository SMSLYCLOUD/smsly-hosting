"""
Views for scoped Docker network configuration.
"""

from rest_framework import permissions, viewsets

from ..models.network_scope import ScopedNetwork
from ..serializers.network_scope import ScopedNetworkSerializer


class ScopedNetworkViewSet(viewsets.ModelViewSet):
    """CRUD for scoped Docker network configurations."""

    queryset = ScopedNetwork.objects.all().select_related("content_type")
    serializer_class = ScopedNetworkSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        scope_type = self.request.query_params.get("scope_type")
        object_id = self.request.query_params.get("object_id")
        if scope_type:
            qs = qs.filter(content_type__model=scope_type)
        if object_id:
            qs = qs.filter(object_id=object_id)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        self._reconcile_bridge(instance)

    def perform_update(self, serializer):
        # Capture the pre-save bridge: a network rename orphans the old
        # bridge's tagged rules (they reference a live interface, so the
        # stale-rule purger ignores them). Clear both old and new.
        from apps.deployments.models.network_scope import (
            ScopedNetwork as _ScopedNetwork,
        )
        old_name = ""
        try:
            _pre = _ScopedNetwork.objects.filter(
                pk=serializer.instance.pk
            ).first()
            if _pre is not None:
                try:
                    old_name = _ScopedNetwork.resolve_network_name(_pre.scope)
                except Exception:
                    old_name = _pre.network_name or ""
        except Exception:
            pass
        instance = serializer.save()
        self._reconcile_bridge(instance, old_bridge=old_name or None)

    def perform_destroy(self, instance):
        # Capture row state BEFORE delete: after removal the effective
        # config falls back to the parent scope (or global default).
        had_explicit_bridge = bool(
            (getattr(instance, "network_name", "") or "").strip()
            or getattr(instance, "isolated", False)
        )
        scope_obj = None
        try:
            scope_obj = instance.scope
        except Exception:
            pass
        super().perform_destroy(instance)
        # Only clear when the deleted row owned an explicit/isolated
        # bridge. Clearing a SHARED bridge (e.g. default smsly-net,
        # inherited after delete) would drop base rules other scopes
        # rely on until the next beat reconcile.
        self._reconcile_inherited(scope_obj, clear_first=had_explicit_bridge)

    @staticmethod
    def _reconcile_bridge(instance, old_bridge: str | None = None) -> None:
        """Clear + reapply host firewall rules for the scope's bridge.

        Clearing first is what makes NARROWING effective: apply-only
        leaves stale RETURN rules live, so a lockdown in the UI would
        report restricted while the old allows persisted.
        """
        try:
            from apps.deployments.models.network_scope import (
                ScopedNetwork as _ScopedNetwork,
            )
            from apps.deployments.services.network_scope import (
                apply_egress_restrictions,
                clear_scoped_rules,
            )
            scope_obj = None
            try:
                scope_obj = instance.scope
            except Exception:
                pass
            name = _ScopedNetwork.resolve_network_name(scope_obj)
            if not name:
                return
            clear_scoped_rules(name)
            if old_bridge and old_bridge != name:
                clear_scoped_rules(old_bridge)
            apply_egress_restrictions(
                name, list(instance.allowed_egress_networks or []),
            )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ScopedNetwork reconcile failed (rules converge on next "
                "beat reconcile): %s", exc,
            )

    @classmethod
    def _reconcile_inherited(cls, scope_obj, clear_first: bool = True) -> None:
        """Re-apply the inherited (parent/default) config after a row delete."""
        try:
            from apps.deployments.models.network_scope import (
                ScopedNetwork as _ScopedNetwork,
            )
            from apps.deployments.services.network_scope import (
                apply_egress_restrictions,
                clear_scoped_rules,
            )
            name = _ScopedNetwork.resolve_network_name(scope_obj)
            if not name:
                return
            if clear_first:
                clear_scoped_rules(name)
            cfg = _ScopedNetwork.resolve_network_config(scope_obj)
            apply_egress_restrictions(name, list(cfg.get("allowed_egress_networks", [])))
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ScopedNetwork inherited reconcile failed: %s", exc,
            )
