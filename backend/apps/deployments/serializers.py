import logging
import re

from rest_framework import serializers

from .models import (  # type: ignore[attr-defined]  # models re-exports from submodules
    Deployment,
    EnvironmentVariable,
    Region,
    Service,
)
from .models_audit import AuditLog
from .models_backup import BackupSchedule, ServerBackup, ServiceBackup, ServiceSnapshot
from .models_safedeploy import (
    DatabaseClone,
    DeploymentApproval,
    DeploymentArtifact,
    MigrationValidation,
    PreviewEnvironment,
)
from .serializers_transfer import ServerTransferCreateSerializer, ServerTransferSerializer  # noqa: F401

# SECURITY: docker_image strings flow into ``docker pull`` on the
# controller and remote nodes. We restrict the scheme/host to the
# platform's own registry (or a small set of well-known public
# registries) and reject anything that would let a tenant pull
# from a personal registry on the public internet.
#
# The canonical allowlist lives in registry_validation.py to prevent
# policy drift between the API-boundary serializer and internal callers.
from .services.registry_validation import ALLOWED_IMAGE_REGISTRY_HOSTS as _ALLOWED_IMAGE_REGISTRIES


def _validate_docker_image(image: str) -> str:
    """Restrict the ``docker_image`` value to a docker-safe reference
    whose registry host is on the platform allowlist.

    The result is a string like ``registry:5000/foo/bar:tag`` or, for
    a public registry, ``nginx:1.27-alpine``. Anything that resolves
    to a registry not in the allowlist (e.g. a personal registry at
    ``attacker.com``) is rejected so a tenant cannot pull an image
    from a host that the platform does not control.
    """
    if image is None:
        return image
    if not isinstance(image, str) or not image.strip():
        raise serializers.ValidationError(
            "docker_image must be a non-empty string."
        )
    image = image.strip()
    # Reject obvious shell-injection characters
    if any(c in image for c in ("\n", "\r", "\t", ";", "&", "|", "`", "$", " ", "<", ">")):
        raise serializers.ValidationError(
            "docker_image must not contain whitespace or shell metacharacters."
        )
    # Split into registry/repo:tag
    # Docker reference grammar: [REGISTRY/]REPO[:TAG][@DIGEST]
    first_slash = image.find("/")
    if first_slash == -1 or (
        # The "registry" portion must contain a '.' or ':' or be 'localhost'
        # — otherwise it's a Docker Hub library reference (e.g. 'nginx')
        not ("." in image[:first_slash] or ":" in image[:first_slash] or image[:first_slash] == "localhost")
    ):
        # Treat as Docker Hub library reference
        registry_prefix = "docker.io"
    else:
        registry_prefix = image[:first_slash]
    if not any(
        registry_prefix == allowed or registry_prefix.startswith(allowed + "/")
        for allowed in _ALLOWED_IMAGE_REGISTRIES
    ):
        raise serializers.ValidationError(
            f"docker_image registry {registry_prefix!r} is not on the platform allowlist. "
            f"Allowed: {', '.join(_ALLOWED_IMAGE_REGISTRIES)}."
        )
    return image


from apps.deployments.models_registry import RegistryCredential


class RegistryCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = RegistryCredential
        fields = ['id', 'name', 'provider', 'registry_url', 'username', 'password', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'password': {'write_only': True},
        }

class RegionSerializer(serializers.ModelSerializer):
    """Serializer for Regions."""
    class Meta:
        model = Region
        fields = '__all__'


logger = logging.getLogger(__name__)


class EnvVarSerializer(serializers.ModelSerializer):
    """
    Serializer for Environment Variables.
    Renamed from EnvironmentVariableSerializer to match view import.
    """
    class Meta:
        model = EnvironmentVariable
        fields = ['id', 'key', 'value', 'is_secret', 'is_locked', 'source']

    def to_representation(self, instance):
        try:
            ret = super().to_representation(instance)
        except Exception as exc:  # pragma: no cover - depends on corrupted DB data
            logger.error(
                "Failed to serialize env var id=%s key=%s service_id=%s: %s",
                getattr(instance, "id", None),
                getattr(instance, "key", None),
                getattr(instance, "service_id", None),
                exc,
            )
            ret = {
                'id': getattr(instance, 'id', None),
                'key': getattr(instance, 'key', ''),
                'value': '',
                'is_secret': bool(getattr(instance, 'is_secret', False)),
                'source': getattr(instance, 'source', 'USER'),
            }
        # Mask secret values by default. Some endpoints (e.g. service env var editor)
        # can opt-in to revealing secrets by passing `reveal_secrets=True` in context.
        reveal_secrets = bool(self.context.get('reveal_secrets', False))
        if instance.is_secret and not reveal_secrets:
            ret['value'] = '********'
        return ret


class ServiceSerializer(serializers.ModelSerializer):
    env_vars = EnvVarSerializer(many=True, required=False)
    regions = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Region.objects.all(), required=False)
    primary_region = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.all(), required=False)
    server_id = serializers.SerializerMethodField()
    repository_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    docker_image = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    registry_credential = serializers.PrimaryKeyRelatedField(
        queryset=RegistryCredential.objects.all(), required=False, allow_null=True
    )
    latest_deployment = serializers.SerializerMethodField()
    service_url = serializers.SerializerMethodField()
    project_name = serializers.CharField(
        source='project.name', read_only=True, default=None)
    project_slug = serializers.CharField(
        source='project.slug', read_only=True, default=None)
    project_emoji = serializers.CharField(
        source='project.icon_emoji', read_only=True, default=None)
    estimated_cost = serializers.SerializerMethodField()
    node_metadata = serializers.SerializerMethodField()
    domain_instances = serializers.SerializerMethodField()

    def validate_docker_image(self, value):
        if not value:
            return value
        from apps.deployments.services.registry_validation import validate_image_registry
        try:
            user = None
            if self.instance and self.instance.owner:
                user = self.instance.owner
            elif 'request' in self.context and self.context['request'].user.is_authenticated:
                user = self.context['request'].user

            class MockService:
                owner_id = user.id if user else None

            return validate_image_registry(value, service=MockService())
        except ValueError as e:
            raise serializers.ValidationError(str(e))

    def validate_name(self, value):
        # SECURITY: Service.name flows into docker container names,
        # traefik labels, and image tags. Auto-slugify rather than
        # reject: replace invalid characters with '-', lowercase,
        # strip leading/trailing separators, truncate to 63 chars.
        if not value or not isinstance(value, str):
            raise serializers.ValidationError("name is required.")
        value = value.strip()
        slug = re.sub(r'[^a-zA-Z0-9_.-]', '-', value).lower()
        slug = re.sub(r'-{2,}', '-', slug)
        slug = slug.strip('-_').strip()
        if not slug:
            raise serializers.ValidationError(
                "name must contain at least one alphanumeric character."
            )
        slug = slug[:63].rstrip('-_').strip()
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9_.]{0,61}[a-z0-9])?", slug):
            raise serializers.ValidationError(
                "name must start and end with a letter or digit "
                "(max 63 chars)."
            )
        return slug

    class Meta:
        model = Service
        fields = '__all__'
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'owner',
            'server',
            'verification_token']

    def get_service_url(self, obj: Service) -> str:
        """Railway-style auto-generated URL."""
        if obj.public_domain and not getattr(obj, "public_domain_hidden", False):
            return f"https://{obj.public_domain}"
        slug = obj.name.lower().replace(' ', '-')
        base_domain = Service.default_public_base_domain()
        return f"https://{slug}.{base_domain}"

    def get_latest_deployment(self, obj: Service) -> dict | None:
        dep = obj.deployments.order_by('-created_at').first()
        if not dep:
            return None
        return {
            'id': str(dep.id),
            'status': dep.status,
            'commit_hash': dep.commit_hash or '',
            'created_at': dep.created_at.isoformat() if dep.created_at else None,
            'vulnerability_report': dep.vulnerability_report,
        }

    def get_node_metadata(self, obj: Service) -> dict:
        server = obj.server
        latest_deploy = (
            obj.deployments
            .filter(status=Deployment.Status.ACTIVE)
            .order_by('-created_at')
            .first()
            or obj.deployments.order_by('-created_at').first()
        )
        if not server and latest_deploy and latest_deploy.target_server:
            server = latest_deploy.target_server

        active_target_type = obj.active_target_type
        active_host = obj.active_host_ip
        if (
            latest_deploy
            and latest_deploy.target_server
            and not getattr(latest_deploy, 'target_is_local', False)
            and (
                not server
                or server.is_primary
                or str(server.id) != str(latest_deploy.target_server_id)
                or str(active_target_type or '').lower() == 'local'
            )
        ):
            server = latest_deploy.target_server
            active_target_type = (
                'lite_agent' if getattr(server, 'is_lite_agent', False) else 'remote'
            )
            active_host = (
                latest_deploy.verified_host_ip
                or getattr(server, 'wg_address', None)
                or getattr(server, 'private_ip', None)
                or getattr(server, 'host', None)
            )

        if not server and active_host:
            from apps.deployments.models_core import ManagedServer
            server = ManagedServer.objects.filter(host=active_host).first()
            if not server:
                server = ManagedServer.objects.filter(private_ip=active_host).first()
            if not server:
                server = ManagedServer.objects.filter(wg_address=active_host).first()

        if active_target_type:
            target_type_label = active_target_type.replace('_', ' ').title()
            if target_type_label == "Remote":
                target_type_label = "Remote Server"

            srv_name = server.name if server else "Unknown Server"
            srv_id = str(server.id) if server else "unknown"
            if (server and server.is_primary and target_type_label == "Local") or (not server and target_type_label == "Local"):
                srv_name = "Local Server"
                srv_id = "local"

            return {
                "id": srv_id,
                "name": srv_name,
                "target_type": target_type_label,
                "host": active_host or (server.host if server else "Unknown"),
                "status": (server.status.lower() if server and server.status else "active")
            }

        if server:
            target_type_label = (
                "Local"
                if server.is_primary
                else ("Lite Agent" if getattr(server, "is_lite_agent", False) else "Remote Server")
            )
            return {
                "id": "local" if server.is_primary else str(server.id),
                "name": "Local Server" if server.is_primary else server.name,
                "target_type": target_type_label,
                "host": server.host,
                "status": server.status.lower() if server.status else "active"
            }

        return {
            "id": "local",
            "name": "Local Server",
            "target_type": "Local",
            "host": "127.0.0.1",
            "status": "active"
        }


    def get_server_id(self, obj: Service) -> str | None:
        return str(obj.server_id) if obj.server_id else None

    def get_estimated_cost(self, obj: Service) -> dict:
        import os
        if str(os.getenv("PLATFORM_COST_ESTIMATION_ENABLED", "true")).lower() not in ("1", "true", "yes", "on"):
            return {"enabled": False}
        node_monthly_cost = float(os.getenv("PLATFORM_DEFAULT_NODE_MONTHLY_COST", "3.00"))
        node_ram_mb = float(max(1, int(os.getenv("PLATFORM_DEFAULT_NODE_RAM_MB", "2048"))))
        service_ram_mb = float(getattr(obj, "memory_mb", 0) or 0)
        weight = min(1.0, max(0.01, service_ram_mb / node_ram_mb))
        monthly = round(node_monthly_cost * weight, 2)
        return {
            "enabled": True,
            "currency": os.getenv("PLATFORM_COST_CURRENCY", "USD"),
            "monthly": monthly,
            "basis": "ram_weighted",
            "confidence": "medium",
            "breakdown": {
                "node_monthly_cost": node_monthly_cost,
                "service_ram_mb": service_ram_mb,
                "node_ram_mb": node_ram_mb,
                "weight": round(weight, 4),
            },
        }

    def create(self, validated_data):
        env_vars_data = validated_data.pop('env_vars', [])
        regions_data = validated_data.pop('regions', [])

        service = Service.objects.create(**validated_data)

        for env in env_vars_data:
            EnvironmentVariable.objects.create(service=service, **env)

        if regions_data:
            service.regions.set(regions_data)

        return service

    def update(self, instance, validated_data):
        regions_data = validated_data.pop('regions', None)
        instance = super().update(instance, validated_data)

        if regions_data is not None:
            instance.regions.set(regions_data)

        return instance

    def get_domain_instances(self, obj):
        if not hasattr(obj, 'domain_instances'):
            return []
        return [
            {
                "domain_name": d.domain_name,
                "status": d.status,
                "dns_expected": d.dns_expected,
                "dns_actual": d.dns_actual,
                "last_error": d.last_error,
                "verified": d.verified,
                "ssl_active": d.ssl_active,
                "issued_at": d.issued_at,
                "expires_at": d.expires_at,
            }
            for d in obj.domain_instances.all()
        ]


class DeploymentSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = '__all__'


class DeploymentTimelineSerializer(serializers.ModelSerializer):
    """Lightweight serializer for deployment timeline view."""
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = [
            'id', 'service', 'service_name', 'commit_hash',
            'commit_message', 'status', 'is_rollback',
            'ai_diagnosis',
            'started_at', 'finished_at', 'duration_seconds',
            'created_at',
        ]


class DeploymentTriggerSerializer(serializers.Serializer):
    service_id = serializers.UUIDField()
    provider_id = serializers.UUIDField()
    commit_hash = serializers.CharField(required=False, allow_blank=True)
    skip_review = serializers.BooleanField(default=False)

    # Optional overrides
    cpu_cores = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False)
    memory_mb = serializers.IntegerField(required=False)

    # Optional custom registry (if set, auto-creates a scoped project)
    registry_url = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Custom registry URL for this deployment. "
                  "If set, a new Project is auto-created and the "
                  "registry is scoped to it.")
    registry_username = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Username for custom registry authentication")
    registry_password = serializers.CharField(
        required=False, allow_blank=True, write_only=True,
        help_text="Password for custom registry authentication")


class InstantRollbackSerializer(serializers.Serializer):
    """Serializer for instant rollback — no body required, but allows
    an optional message."""
    message = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        help_text="Optional reason for rollback")


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'


class DeploymentApproveSerializer(serializers.Serializer):
    """Accept optional overrides when approving a deployment review."""
    cpu_cores = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False,
        help_text="Override CPU cores")
    memory_mb = serializers.IntegerField(
        required=False,
        help_text="Override memory in MB")
    env_overrides = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        help_text="Dict of env var key→value to add/update before build")

class ServiceBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceBackup
        fields = [
            'id', 'service', 'created_by', 'label', 'status', 'db_only',
            'backup_type', 'size_bytes', 'error_message',
            'created_at', 'completed_at',
            'cloud_uploaded', 'cloud_destination', 'cloud_bucket',
        ]
        read_only_fields = [
            'id', 'created_by', 'status', 'size_bytes', 'error_message',
            'created_at', 'completed_at', 'cloud_uploaded', 'cloud_bucket',
        ]

class ServerBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerBackup
        fields = [
            'id', 'label', 'status', 'db_only', 'size_bytes',
            'services_included', 'error_message',
            'created_at', 'completed_at',
            'cloud_uploaded', 'cloud_destination', 'cloud_bucket',
        ]
        read_only_fields = [
            'id', 'status', 'size_bytes', 'services_included',
            'error_message', 'created_at', 'completed_at',
            'cloud_uploaded', 'cloud_bucket',
        ]

class BackupScheduleSerializer(serializers.ModelSerializer):
    cloud_destination_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = BackupSchedule
        fields = '__all__'
        extra_kwargs = {
            's3_access_key': {'write_only': True},
            's3_secret_key': {'write_only': True},
        }

    def _apply_cloud_destination(self, validated_data):
        from .models_cloud_storage import CloudStorageDestination
        cloud_destination_id = validated_data.pop('cloud_destination_id', None)
        if cloud_destination_id:
            try:
                dest = CloudStorageDestination.objects.get(id=cloud_destination_id)
                # Apply fields temporarily in data dict
                validated_data['storage_backend'] = 's3'
                validated_data['s3_bucket'] = dest.bucket
                validated_data['s3_region'] = dest.region
                validated_data['s3_endpoint'] = dest.endpoint
                validated_data['s3_access_key'] = dest.access_key
                validated_data['s3_secret_key'] = dest.secret_key
            except CloudStorageDestination.DoesNotExist:
                pass

    def create(self, validated_data):
        self._apply_cloud_destination(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._apply_cloud_destination(validated_data)
        return super().update(instance, validated_data)

    def validate_s3_endpoint(self, value):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .models_backup import validate_endpoint_url
        try:
            validate_endpoint_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class SnapshotScheduleSerializer(serializers.ModelSerializer):
    cloud_destination_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        from apps.deployments.models_backup import SnapshotSchedule
        model = SnapshotSchedule
        fields = '__all__'
        extra_kwargs = {
            's3_access_key': {'write_only': True},
            's3_secret_key': {'write_only': True},
        }

    def create(self, validated_data):
        dest_id = validated_data.pop('cloud_destination_id', None)
        instance = super().create(validated_data)
        if dest_id:
            from apps.deployments.models_cloud_storage import CloudStorageDestination
            dest = CloudStorageDestination.objects.filter(id=dest_id).first()
            if dest:
                dest.apply_to_schedule(instance)
        return instance

    def update(self, instance, validated_data):
        dest_id = validated_data.pop('cloud_destination_id', None)
        instance = super().update(instance, validated_data)
        if dest_id:
            from apps.deployments.models_cloud_storage import CloudStorageDestination
            dest = CloudStorageDestination.objects.filter(id=dest_id).first()
            if dest:
                dest.apply_to_schedule(instance)
        elif 'cloud_destination_id' in self.initial_data and self.initial_data['cloud_destination_id'] is None:
            instance.storage_backend = 'local'
            instance.s3_bucket = ''
            instance.s3_access_key = ''
            instance.s3_secret_key = ''
            instance.s3_endpoint = ''
            instance.save()
        return instance


class ServiceSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceSnapshot
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'config_data', 'diff_summary', 'parent_snapshot']


class ServiceSnapshotRestoreSerializer(serializers.Serializer):
    target_service_id = serializers.UUIDField(required=False, allow_null=True)
    redeploy = serializers.BooleanField(default=False)


class ServiceSnapshotDiffSerializer(serializers.Serializer):
    compare_with_id = serializers.UUIDField(required=True)


# --- SafeDeploy Serializers ---

class PreviewCreateSerializer(serializers.Serializer):
    branch_name = serializers.CharField(max_length=255, min_length=1, trim_whitespace=True)
    commit_sha = serializers.CharField(max_length=64, min_length=7)

    def validate_branch_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("branch_name must not be blank")
        if re.search(r'[^a-zA-Z0-9._\-/]', value):
            raise serializers.ValidationError("branch_name contains invalid characters")
        return value

    def validate_commit_sha(self, value):
        if not re.match(r'^[0-9a-fA-F]{7,64}$', value):
            raise serializers.ValidationError("commit_sha must be a valid hex SHA (7-64 characters)")
        return value


class PreviewRebuildSerializer(serializers.Serializer):
    commit_sha = serializers.CharField(max_length=64, min_length=7, required=False)

    def validate_commit_sha(self, value):
        if not re.match(r'^[0-9a-fA-F]{7,64}$', value):
            raise serializers.ValidationError("commit_sha must be a valid hex SHA (7-64 characters)")
        return value


class ApprovalApproveSerializer(serializers.Serializer):
    pass


class ApprovalRejectSerializer(serializers.Serializer):
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True, trim_whitespace=True)


class ApprovalCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentApproval
        fields = ['service', 'deployment', 'preview_environment', 'requested_by', 'risk_level', 'approval_notes']


class DatabaseCloneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatabaseClone
        fields = '__all__'

class MigrationValidationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MigrationValidation
        fields = '__all__'

class DeploymentArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentArtifact
        fields = '__all__'

class DeploymentApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentApproval
        fields = '__all__'

class PreviewEnvironmentSerializer(serializers.ModelSerializer):
    database_clone = DatabaseCloneSerializer(read_only=True)
    migration_validation = MigrationValidationSerializer(read_only=True)
    artifacts = DeploymentArtifactSerializer(many=True, read_only=True)

    class Meta:
        model = PreviewEnvironment
        fields = '__all__'
