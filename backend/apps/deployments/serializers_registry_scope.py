"""
Serializers for ScopedRegistry.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from .models_registry_scope import ScopedRegistry


class ScopedRegistrySerializer(serializers.ModelSerializer):
    """Read/write serializer for ScopedRegistry.

    Accepts ``scope_type`` (``organization``|``team``|``project``) and
    ``scope_id`` as write-only fields.  The ``create()`` method resolves
    them into a ``content_type`` / ``object_id`` pair automatically.
    """

    scope_type = serializers.ChoiceField(
        choices=["organization", "team", "project"],
        write_only=True,
    )
    scope_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = ScopedRegistry
        fields = [
            "id",
            "scope_type",
            "scope_id",
            "registry_url",
            "username",
            "password",
            "is_internal",
            "allowed_registry_hosts",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "password": {"write_only": True},
        }

    def create(self, validated_data):
        scope_type = validated_data.pop("scope_type")
        scope_id = validated_data.pop("scope_id")
        ct = ContentType.objects.get(model=scope_type)
        validated_data["content_type"] = ct
        validated_data["object_id"] = scope_id
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Strip write-only scope fields that are not model attributes.
        # These are only used during create() to resolve the GenericForeignKey.
        validated_data.pop("scope_type", None)
        validated_data.pop("scope_id", None)
        return super().update(instance, validated_data)


class ScopedRegistryReadSerializer(serializers.ModelSerializer):
    """Lightweight read serializer — no password, no write-only fields."""

    scope_type = serializers.SerializerMethodField()
    scope_label = serializers.SerializerMethodField()

    class Meta:
        model = ScopedRegistry
        fields = [
            "id",
            "scope_type",
            "scope_label",
            "registry_url",
            "is_internal",
            "allowed_registry_hosts",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def get_scope_type(self, obj) -> str | None:
        if obj.content_type:
            return obj.content_type.model
        return None

    def get_scope_label(self, obj) -> str:
        return str(obj.scope) if obj.scope else "(orphaned)"
