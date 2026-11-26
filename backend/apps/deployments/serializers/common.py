from django.core.exceptions import ValidationError as DjValidationError
from rest_framework import serializers

from apps.core.validators import validate_ssrf
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

    def validate_registry_url(self, value: str) -> str:
        url = (value or '').strip()
        if not url:
            return url
        request = self.context.get('request')
        is_admin = bool(
            request
            and getattr(request, 'user', None)
            and (request.user.is_staff or request.user.is_superuser)
        )
        if '://' in url and url.lower().startswith('http://') and not is_admin:
            raise serializers.ValidationError('Registry URL must use https:// for tenant registries.')
        if not is_admin:
            check_target = url if '://' in url else f'https://{url}'
            try:
                validate_ssrf(check_target, fail_closed_dns=False)
            except DjValidationError as exc:
                raise serializers.ValidationError(str(exc))
        return url


class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = [
            'id', 'name', 'slug', 'provider',
            'country_code', 'city', 'lat', 'lng', 'is_active',
        ]
