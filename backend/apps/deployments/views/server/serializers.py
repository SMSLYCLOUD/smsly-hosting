"""
Serializers and throttle classes for multi-server management views.
"""

import logging

from rest_framework import serializers
from rest_framework.throttling import UserRateThrottle

from ...models.servers import ManagedServer

logger = logging.getLogger(__name__)
class ManagedServerSerializer(serializers.ModelSerializer):
    has_ssh_credentials = serializers.SerializerMethodField()
    # SECURITY (Batch G cont): whether a TLS cert SHA-256 pin
    # is configured. We never return the pin itself — only a
    # boolean — so the serializer is safe to surface in the
    # read API. The pin is set via a separate admin action.
    tls_cert_sha256_set = serializers.SerializerMethodField()

    def get_has_ssh_credentials(self, obj):
        return bool(str(obj.ssh_password or '').strip() or str(obj.ssh_key or '').strip())

    class Meta:
        model = ManagedServer
        fields = [
            "id", "name", "host", "private_ip", "api_url", "ssh_port",
            "ssh_user", "provider_metadata", "is_primary",
            "allow_user_workloads", "status", "last_health_check",
            "server_version", "services_count", "created_at",
            "provision_status", "provision_logs", "role", "wg_address",
            "has_ssh_credentials", "is_lite_agent",
            # Agent self-registration signals: surfaced so operators
            # can tell at a glance whether the agent's installer
            # has finished bootstrapping and how recently the
            # registrar last reported in. See models_core.py for
            # the field-level rationale.
            "agent_ready", "last_agent_heartbeat_at", "agent_runtime_info",
            # SECURITY (Batch G cont): expose the per-server TLS
            # verification settings so operators can audit which
            # nodes run with verify_tls=False. tls_cert_sha256 is
            # write-only (the pin is never echoed back to API
            # consumers); tls_cert_sha256_set is a boolean indicator
            # so operators can see whether a pin is configured
            # without leaking the pin value itself.
            "verify_tls", "tls_cert_sha256_set",
        ]
        read_only_fields = [
            "id", "status", "last_health_check", "server_version",
            "services_count", "created_at", "provision_status",
            "role", "wg_address", "has_ssh_credentials", "is_lite_agent",
            "agent_ready", "last_agent_heartbeat_at", "agent_runtime_info",
            "tls_cert_sha256_set",
        ]

    def get_tls_cert_sha256_set(self, obj):
        """Return whether a TLS cert SHA-256 pin is configured, without
        revealing the pin itself."""
        return bool((getattr(obj, "tls_cert_sha256", "") or "").strip())


class ManagedServerCreateSerializer(serializers.ModelSerializer):
    """For 'Connect Existing' mode — user provides api_url + api_token."""
    node_certificate = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "private_ip", "api_url", "api_token",
            "gateway_secret", "ssh_user", "ssh_password", "ssh_key",
            "ssh_port", "is_primary", "allow_user_workloads",
            "provider_metadata", "is_lite_agent", "node_certificate",
        ]
        extra_kwargs = {
            "api_token": {"write_only": True, "required": False},
            "gateway_secret": {"write_only": True, "required": False},
            "ssh_key": {"write_only": True, "required": False, "trim_whitespace": False},
            "ssh_password": {"write_only": True, "required": False},
            "provider_metadata": {"required": False},
        }

    def validate(self, data):
        is_lite = data.get("is_lite_agent", False)
        if is_lite:
            cert = (data.get("node_certificate") or "").strip()
            if not cert:
                raise serializers.ValidationError(
                    {"node_certificate": "node_certificate is required when is_lite_agent=True."}
                )
        return data

    def create(self, validated_data):
        cert = validated_data.pop("node_certificate", None)
        if cert and cert.strip():
            import hashlib
            validated_data["tls_cert_sha256"] = hashlib.sha256(cert.strip().encode('utf-8')).hexdigest()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        cert = validated_data.pop("node_certificate", None)
        if cert and cert.strip():
            import hashlib
            validated_data["tls_cert_sha256"] = hashlib.sha256(cert.strip().encode('utf-8')).hexdigest()
        return super().update(instance, validated_data)

    def validate_ssh_key(self, value):
        if value and value.strip():
            key = value.strip()
            if not key.startswith('-----BEGIN '):
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Must be a valid PEM-encoded private key "
                    "starting with '-----BEGIN ... PRIVATE KEY-----'."
                )
            if '-----END ' not in key:
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Missing '-----END ... PRIVATE KEY-----' footer."
                )
        return value

    def to_representation(self, instance):
        # Return the stable read serializer shape after create/update operations.
        return ManagedServerSerializer(instance).data

    def validate_host(self, value):
        """Strip protocol and port, then enforce safe-IP policy for non-primary servers."""
        import re
        value = re.sub(r'^https?://', '', (value or "")).strip().rstrip('/')
        value = re.sub(r':\d+$', '', value)
        if not value:
            raise serializers.ValidationError("Host is required.")
        is_primary = self.initial_data.get("is_primary", False)
        if not is_primary:
            try:
                import ipaddress as _ip
                ip = _ip.ip_address(value)
                if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                    raise serializers.ValidationError(
                        f"Host {value} is in a forbidden range for non-primary servers."
                    )
            except ValueError:
                pass  # Hostname — allowed for non-primary
        if value.lower() == "localhost" and not is_primary:
            raise serializers.ValidationError("'localhost' is not allowed as a non-primary host.")
        return value

    def validate_api_url(self, value):
        """Ensure api_url has a protocol prefix. Default to HTTP for IPs.

        SECURITY (Batch G): reject api_url that points at any
        non-public address (RFC1918, link-local, loopback,
        multicast, reserved, unspecified). These are SSRF targets
        that the operator (not the user) should be able to reach.
        A user that can register a server with api_url pointing at
        the platform's own controller would otherwise be able to
        relay requests to the controller's admin endpoints via
        the ``/proxy/`` action.
        """
        import ipaddress
        from urllib.parse import urlparse
        value = (value or "").strip().rstrip('/')
        if value and not value.startswith(('http://', 'https://')):
            host_part = value.split(':')[0]
            try:
                ipaddress.ip_address(host_part)
                value = f'http://{value}'
            except ValueError:
                value = f'https://{value}'
        if value:
            parsed = urlparse(value)
            hostname = (parsed.hostname or '').lower()
            if hostname in ('localhost',) or hostname.endswith('.localhost'):
                raise serializers.ValidationError(
                    f"api_url hostname {hostname!r} is a loopback / internal target."
                )
            try:
                ip = ipaddress.ip_address(hostname)
                # SECURITY: reject ALL non-global unicast addresses.
                # A valid user-registered node has a public IP
                # (the operator's VPS), so anything in private
                # ranges is an SSRF target.
                from django.conf import settings
                allow_local = getattr(settings, 'ALLOW_LOCAL_NODES', False)
                if not allow_local:
                    if not ip.is_global or (
                        ip.is_loopback or ip.is_link_local
                        or ip.is_multicast or ip.is_reserved
                        or ip.is_unspecified or ip.is_private
                    ):
                        raise serializers.ValidationError(
                            f"api_url IP {ip} is not a public address "
                            f"(loopback / private / link-local / reserved)."
                        )
            except ValueError:
                pass  # hostname — allowed
        return value


class ManagedServerProvisionSerializer(serializers.ModelSerializer):
    """For 'Provision New' mode — user provides SSH credentials."""
    ssh_auth_method = serializers.ChoiceField(
        choices=["password", "key"], write_only=True, required=False, default="password"
    )
    node_certificate = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "ssh_port", "ssh_user",
            "ssh_password", "ssh_key", "ssh_auth_method",
            "is_primary", "allow_user_workloads", "is_lite_agent",
            "node_certificate",
        ]
        extra_kwargs = {
            "ssh_password": {"write_only": True},
            "ssh_key": {"write_only": True, "trim_whitespace": False},
        }

    def validate_ssh_key(self, value):
        if value and value.strip():
            key = value.strip()
            if not key.startswith('-----BEGIN '):
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Must be a valid PEM-encoded private key "
                    "starting with '-----BEGIN ... PRIVATE KEY-----'."
                )
            if '-----END ' not in key:
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Missing '-----END ... PRIVATE KEY-----' footer."
                )
        return value

    def validate(self, data):
        method = data.get("ssh_auth_method", "password")
        if method == "password" and not data.get("ssh_password"):
            raise serializers.ValidationError(
                {"ssh_password": "Password is required for password auth."}
            )
        if method == "key" and not data.get("ssh_key"):
            raise serializers.ValidationError(
                {"ssh_key": "SSH private key is required for key auth."}
            )
        # If provisioning via SSH, we don't require the certificate upfront.
        # The provisioner script will automatically fetch it from the remote node
        # once the lite agent is installed.
        return data


# ─── ViewSet ─────────────────────────────────────────────────────────────────

class ServerCommandThrottle(UserRateThrottle):
    scope = 'server_run_command'


class ServerHealThrottle(UserRateThrottle):
    scope = 'server_heal'


class ServerProxyThrottle(UserRateThrottle):
    scope = 'server_proxy'


ALLOWED_PROXY_METHODS = {'GET', 'HEAD'}
ALLOWED_PROXY_PATHS = (
    '/api/v1/health',
    '/api/v1/metrics',
)


class ServerCheckAllThrottle(UserRateThrottle):
    scope = 'server_check_all'


class ServerProvisionThrottle(UserRateThrottle):
    scope = 'server_provision'

