from rest_framework import serializers

from ..models import Region
from ..models.registry import RegistryCredential
from ..services.registry_validation import all_allowed_registry_hosts as _all_allowed_registry_hosts


def _validate_docker_image(image: str) -> str:
    if image is None:
        return image
    if not isinstance(image, str) or not image.strip():
        raise serializers.ValidationError(
            "docker_image must be a non-empty string."
        )
    image = image.strip()
    if any(c in image for c in ("\n", "\r", "\t", ";", "&", "|", "`", "$", " ", "<", ">")):
        raise serializers.ValidationError(
            "docker_image must not contain whitespace or shell metacharacters."
        )
    first_slash = image.find("/")
    if first_slash == -1 or (
        not ("." in image[:first_slash] or ":" in image[:first_slash] or image[:first_slash] == "localhost")
    ):
        registry_prefix = "docker.io"
    else:
        registry_prefix = image[:first_slash]
    _allowed = _all_allowed_registry_hosts()
    if not any(
        registry_prefix == allowed or registry_prefix.startswith(allowed + "/")
        for allowed in _allowed
    ):
        raise serializers.ValidationError(
            f"docker_image registry {registry_prefix!r} is not on the platform allowlist. "
            f"Allowed: {', '.join(_allowed)}."
        )
    return image


class RegistryCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = RegistryCredential
        fields = ['id', 'name', 'provider', 'registry_url', 'username', 'password', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'password': {'write_only': True},
        }


class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = [
            'id', 'name', 'slug', 'provider',
            'country_code', 'city', 'lat', 'lng', 'is_active',
        ]
