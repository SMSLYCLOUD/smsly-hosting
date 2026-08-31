import logging
import re

from django.db.models import Q
from rest_framework import serializers

from ..models import Deployment, EnvironmentVariable, Region, Service
from ..models.core import ManagedServer
from ..models.registry import RegistryCredential

logger = logging.getLogger(__name__)


def _get_latest_deployment(obj) -> dict | None:
    deployments = getattr(obj, '_prefetched_deployments', None)
    if deployments is not None:
        dep = deployments[0] if deployments else None
    else:
        dep = obj.deployments.order_by('-created_at').first()
    if not dep:
        return None
    target_server_name = None
    if dep.target_server_id:
        target_server_name = getattr(dep.target_server, 'name', None)
    elif dep.target_is_local:
        target_server_name = 'Local Server'
    return {
        'id': str(dep.id),
        'status': dep.status,
        'commit_hash': dep.commit_hash or '',
        'created_at': dep.created_at.isoformat() if dep.created_at else None,
        'vulnerability_report': dep.vulnerability_report,
        'target_server': str(dep.target_server_id) if dep.target_server_id else None,
        'target_server_name': target_server_name,
        'target_is_local': dep.target_is_local,
    }


def _get_node_metadata(obj) -> dict:
    server = obj.server
    active_deps = getattr(obj, '_active_deployments', None)
    if active_deps is not None:
        latest_deploy = active_deps[0] if active_deps else None
    else:
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
        server = ManagedServer.objects.filter(
            Q(host=active_host) | Q(private_ip=active_host) | Q(wg_address=active_host)
        ).first()

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


class EnvVarSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvironmentVariable
        fields = ['id', 'key', 'value', 'is_secret', 'is_locked', 'source']
        read_only_fields = ['id']

    def validate_key(self, value):
        if not value or not isinstance(value, str):
            raise serializers.ValidationError("key is required.")
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
            raise serializers.ValidationError(
                "key must be a valid environment variable name "
                "(letters, digits, underscores; start with letter or underscore)."
            )
        return value

    def to_representation(self, instance):
        try:
            ret = super().to_representation(instance)
        except Exception as exc:
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
        reveal_secrets = bool(self.context.get('reveal_secrets', False))
        if instance.is_secret and not reveal_secrets:
            ret['value'] = '********'
        return ret


class ServiceListSerializer(serializers.ModelSerializer):
    latest_deployment = serializers.SerializerMethodField()
    node_metadata = serializers.SerializerMethodField()
    node_url = serializers.SerializerMethodField()
    running_replicas = serializers.SerializerMethodField()
    internal_addresses = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = [
            'id', 'name', 'status', 'owner', 'project',
            'server', 'public_domain', 'custom_domains', 'internal_port',
            'health_status', 'deploy_type', 'buildpack', 'created_at',
            'updated_at', 'latest_deployment', 'node_metadata', 'node_url',
            'wildcard_url_enabled', 'node_url_enabled',
            'wildcard_redirect_custom_domain', 'wildcard_internal_only',
            'path_redirects', 'host_aliases',
            'env_scan_depth', 'running_replicas',
            'internal_addresses',
        ]

    def get_running_replicas(self, obj):
        return getattr(obj, 'running_replicas_count', 0)

    def get_internal_addresses(self, obj):
        """Cached at serialization time so the service list page can
        show each service's internal IP without an N+1 Docker inspect
        burst."""
        if not getattr(obj, '_internal_addresses_cache', None):
            obj._internal_addresses_cache = obj.generate_internal_addresses()
        return obj._internal_addresses_cache

    def get_effective_registry(self, obj):
        """The registry host this service's images actually push/pull to.

        Walks the ScopedRegistry chain (service → project → team → org)
        and falls back to the platform's configured registry. Surfaced so
        the Advanced tab can pre-fill the image name with the REAL
        registry instead of a hardcoded domain (the old UI fabricated
        'registry.Trulay.co/<name>' for every service and then persisted
        that bogus value on save).
        """
        try:
            from ..models.registry_scope import ScopedRegistry
            url = ScopedRegistry.resolve_registry_url(obj.project) if obj.project_id else None
            if not url:
                # Service-level credential (external registry) wins if set.
                cred = getattr(obj, 'registry_credential', None)
                if cred and getattr(cred, 'registry_url', ''):
                    url = cred.registry_url
            if not url:
                return None
            return url.replace('https://', '').replace('http://', '').rstrip('/')
        except Exception:
            return None

    def get_latest_deployment(self, obj: Service) -> dict | None:
        return _get_latest_deployment(obj)

    def get_node_metadata(self, obj: Service) -> dict:
        return _get_node_metadata(obj)

    def get_node_url(self, obj: Service) -> str | None:
        from apps.deployments.services.caddy_manager.config_generation import _resolve_effective_server
        svr = _resolve_effective_server(obj)
        if not svr or getattr(svr, 'is_primary', False):
            return None
        node_number = getattr(svr, 'node_number', None) or 1
        base_domain = Service.default_public_base_domain()
        slug = obj.name.lower().replace(' ', '-')
        parts = base_domain.split(".")
        if len(parts) > 2:
            return f"https://{slug}.grid{node_number}.{'.'.join(parts[1:])}"
        return f"https://{slug}.grid{node_number}.{base_domain}"


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
    node_url = serializers.SerializerMethodField()
    internal_addresses = serializers.SerializerMethodField()
    effective_registry = serializers.SerializerMethodField()
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
        from ..services.registry_validation import validate_image_registry
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

    def validate_host_aliases(self, value):
        if not value:
            return value
        if not isinstance(value, list):
            raise serializers.ValidationError("host_aliases must be a list.")
        # Prevent subdomain squatting via host_aliases — same check as custom_domains
        from apps.domains.utils import normalize_domain, split_host_and_path
        seen = set()
        normalized_aliases = []
        for entry in value:
            if not isinstance(entry, dict):
                raise serializers.ValidationError("Each host_alias must be an object with 'host'.")
            raw_host = str(entry.get('host') or '').strip().lower()
            if not raw_host:
                continue
            # Accept 'app.example.com/login' as a shortcut: the path becomes
            # the host_alias.rewrite_root and the bare host is what we route on.
            # This mirrors the domain/path format the operator types in the UI.
            try:
                host, path = split_host_and_path(raw_host)
            except ValueError as e:
                raise serializers.ValidationError(f"Invalid host_alias '{raw_host}': {e}")
            try:
                host = normalize_domain(host)
            except ValueError as e:
                raise serializers.ValidationError(f"Invalid host_alias '{raw_host}': {e}")
            normalized_entry = dict(entry)
            normalized_entry['host'] = host
            # If the operator wrote a path in the host field, prefer it over
            # any existing rewrite_root (or set it when none was given).
            if path and not normalized_entry.get('rewrite_root'):
                normalized_entry['rewrite_root'] = path
            # Dedup key is (bare_host, rewrite_root). Two entries with the
            # same host but different rewrite_roots (e.g. 'accounts.google.com'
            # rewriting / to /login AND /signup to /register) are legitimate
            # and must coexist — each rewrite_root becomes a distinct
            # @alias_root path matcher in the Caddyfile.
            dedup_key = (host, normalized_entry.get('rewrite_root', ''))
            if dedup_key in seen:
                raise serializers.ValidationError(
                    f"Duplicate host_alias '{host}' with rewrite_root "
                    f"'{normalized_entry.get('rewrite_root', '')}'."
                )
            seen.add(dedup_key)
            normalized_aliases.append(normalized_entry)
            # Check global conflict (public_domain, custom_domains, other host_aliases)
            qs = Service.objects.exclude(id=getattr(self.instance, 'id', None) or 0)
            if qs.filter(public_domain=host).exists():
                raise serializers.ValidationError(f"Host '{host}' is already assigned as a public domain.")
            if qs.filter(custom_domains__contains=[host]).exists():
                raise serializers.ValidationError(f"Host '{host}' is already assigned as a custom domain.")
            # Check other services' host_aliases (JSONField contains)
            if Service.objects.exclude(id=getattr(self.instance, 'id', None) or 0).filter(
                host_aliases__contains=[{"host": host}]
            ).exists():
                raise serializers.ValidationError(f"Host '{host}' is already assigned to another service.")
        # Return the normalized list (with the path stripped from host and
        # moved into rewrite_root). Returning the unmodified value here was
        # the root cause of the React #31 crash — the frontend saw the raw
        # {'host': 'app.example.com/login', ...} and React errored when it
        # tried to render that as a child element.
        return normalized_aliases

    class Meta:
        model = Service
        fields = [
            'id', 'name', 'slug', 'status', 'deletion_error',
            'owner', 'server', 'project', 'provider',
            'repository_url', 'branch',
            'deploy_type', 'buildpack',
            'docker_image', 'registry_credential', 'effective_registry',
            'build_command', 'start_command', 'root_directory',
            'internal_port',
            'public_domain', 'public_domain_hidden', 'domain_verified',
            'staging_domain', 'staging_domain_verified',
            'custom_domains',
            'cpu_cores', 'memory_mb',
            'autoscale_enabled', 'min_replicas', 'max_replicas',
            'autoscale_cpu_target', 'vpa_enabled', 'alert_config',
            'disable_crowdsec_waf',
            'regions', 'primary_region',
            'safedeploy_enabled', 'preview_environments_enabled',
            'auto_create_preview_on_branch_push',
            'migration_auto_approval_policy', 'production_requires_backup',
            'auto_rollback_enabled', 'auto_rollback_threshold',
            'deploy_strategy', 'canary_percentage',
            'is_preview', 'parent_service', 'pr_number',
            'health_check_path', 'health_check_port',
            'health_check_interval', 'health_check_timeout',
            'health_check_retries', 'auto_restart', 'health_status',
            'restart_policy',
            'deploy_mode', 'compose_file', 'compose_main_service',
            'is_public',
            'wildcard_url_enabled', 'node_url_enabled',
            'wildcard_redirect_custom_domain', 'wildcard_internal_only',
            'path_redirects', 'host_aliases',
            'active_target_type', 'active_host_ip', 'active_runtime_id',
            'last_scale_at',
            'locked', 'locked_reason', 'restrict_to_creator',
            'allowed_actions', 'restricted_environments',
            'env_scan_depth',
            # Internal network (per-service). The platform-wide
            # internal_addresses SerializerMethodField returns the
            # container IPs on each attached bridge.
            'use_internal_network', 'platform_internal_ip',
            'created_at', 'updated_at',
            # SerializerMethodField / nested fields
            'env_vars', 'server_id',
            'latest_deployment', 'service_url', 'node_url',
            'internal_addresses',
            'project_name', 'project_slug', 'project_emoji',
            'estimated_cost', 'node_metadata', 'domain_instances',
            'running_replicas',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'owner',
            'server',
            'verification_token',
            'compose_file',
            'active_target_type',
            'active_host_ip',
            'active_runtime_id',
            'last_scale_at',
            'staging_domain_verified',
            # Auto-populated at spawn time from the platform-bridge
            # network attachment; users don't set this directly.
            'platform_internal_ip',
        ]

    def get_service_url(self, obj: Service) -> str:
        if obj.public_domain and not getattr(obj, "public_domain_hidden", False):
            return f"https://{obj.public_domain}"
        slug = obj.name.lower().replace(' ', '-')
        base_domain = Service.default_public_base_domain()
        return f"https://{slug}.{base_domain}"

    def get_node_url(self, obj: Service) -> str | None:
        """Return the direct node URL if the service is on a full node."""
        from apps.deployments.services.caddy_manager.config_generation import _resolve_effective_server
        svr = _resolve_effective_server(obj)
        if not svr or getattr(svr, 'is_primary', False):
            return None
        node_number = getattr(svr, 'node_number', None) or 1
        base_domain = Service.default_public_base_domain()
        slug = obj.name.lower().replace(' ', '-')
        parts = base_domain.split(".")
        if len(parts) > 2:
            return f"https://{slug}.grid{node_number}.{'.'.join(parts[1:])}"
        return f"https://{slug}.grid{node_number}.{base_domain}"

    def get_latest_deployment(self, obj: Service) -> dict | None:
        return _get_latest_deployment(obj)

    def get_node_metadata(self, obj: Service) -> dict:
        return _get_node_metadata(obj)

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

    def get_internal_addresses(self, obj):
        """Same as the list serializer's get_internal_addresses — returns
        the container's IP(s) and the Docker networks it's attached to so
        the detail page can show the recommended inter-service URL."""
        if not getattr(obj, '_internal_addresses_cache', None):
            obj._internal_addresses_cache = obj.generate_internal_addresses()
        return obj._internal_addresses_cache
