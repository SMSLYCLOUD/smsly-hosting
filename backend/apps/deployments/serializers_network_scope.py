"""
Serializers for scoped network configuration.
"""

from rest_framework import serializers

from .models_network_scope import ScopedNetwork


class ScopedNetworkSerializer(serializers.ModelSerializer):
    scope_type = serializers.CharField(source="content_type.model", read_only=True)
    scope_name = serializers.SerializerMethodField()

    class Meta:
        model = ScopedNetwork
        fields = [
            "id",
            "content_type",
            "object_id",
            "scope_type",
            "scope_name",
            "network_name",
            "driver",
            "isolated",
            "internal",
            "enable_ipv6",
            "subnet",
            "allow_public_traefik",
            "allowed_egress_networks",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "scope_type", "scope_name", "created_at", "updated_at"]

    def get_scope_name(self, obj) -> str:
        return str(obj.scope) if obj.scope else "(orphaned)"
