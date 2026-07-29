"""
Serializers for scoped network configuration.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from apps.deployments.models.network_scope import ScopedNetwork


class ScopedNetworkSerializer(serializers.ModelSerializer):
    scope_type = serializers.CharField(source="content_type.model", read_only=True)
    scope_name = serializers.SerializerMethodField()
    scope_type_input = serializers.ChoiceField(
        choices=["organization", "team", "project"],
        write_only=True,
        required=False,
    )
    scope_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = ScopedNetwork
        fields = [
            "id",
            "content_type",
            "object_id",
            "scope_type",
            "scope_type_input",
            "scope_id",
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
        extra_kwargs = {
            "content_type": {"required": False},
            "object_id": {"required": False},
        }

    def get_scope_name(self, obj) -> str:
        return str(obj.scope) if obj.scope else "(orphaned)"

    def create(self, validated_data):
        scope_type_val = validated_data.pop("scope_type_input", None) or self.initial_data.get("scope_type")
        scope_id_val = validated_data.pop("scope_id", None) or self.initial_data.get("scope_id")
        if scope_type_val and scope_id_val:
            ct = ContentType.objects.get(model=scope_type_val)
            validated_data["content_type"] = ct
            validated_data["object_id"] = scope_id_val
        elif not validated_data.get("content_type") or not validated_data.get("object_id"):
            from apps.deployments.models.core import Project
            proj = Project.objects.first()
            if proj:
                ct = ContentType.objects.get_for_model(proj)
                validated_data["content_type"] = ct
                validated_data["object_id"] = proj.id
            else:
                raise serializers.ValidationError({"scope_id": "A scope (project/team/organization) or scope_id must be provided."})
        return super().create(validated_data)
