"""
Serializers and throttle classes for multi-server management views.
"""

import logging

from rest_framework import serializers
from rest_framework.throttling import UserRateThrottle

from ...models.servers import ManagedServer

logger = logging.getLogger(__name__)


def _request_user_is_staff(request) -> bool:
    """Staff/superusers register WireGuard-mesh and VPC-internal nodes by
    design — the private-range allowances in the field validators exist
    for them. Everyone else gets the stricter public-target policy."""
    user = getattr(request, "user", None)
    return bool(
        user is not None
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )


def _assert_public_node_target(serializer, field: str, value: str) -> None:
    """SSRF hardening: the field-level literal checks miss DNS rebinding —
    a hostname whose records resolve to a private or metadata address
    (169.254.169.254, 10.x, docker aliases) sails past a string check.
    Require every resolved address to be public for non-admin users;
    staff bypass (private fleets register by design)."""
    if not value:
        return
    request = serializer.context.get("request")
    user = getattr(request, "user", None)
    if user is not None and (
        getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    ):
        return
    from apps.core.validators import validate_ssrf

    try:
        validate_ssrf(value)
    except Exception as exc:
        detail = (
            getattr(exc, "message", None)
            or "; ".join(str(m) for m in getattr(exc, "messages", []))
            or str(exc)
        )
        raise serializers.ValidationError(
            f"Node target rejected (SSRF protection): {detail}"
        )
class ManagedServerSerializer(serializers.ModelSerializer):
    has_ssh_credentials = serializers.SerializerMethodField()
    # Mesh DNS names for this server (gridN/node-name labels under the
    # mesh zone, e.g. grid1.mesh.internal). Pure computation, no DB
    # queries — safe for list views. Consumed by the Servers page so
    # operators address nodes by name instead of raw mesh IPs.
    mesh_dns_names = serializers.SerializerMethodField()
    # SECURITY (Batch G cont): whether a TLS cert SHA-256 pin
    # is configured. We never return the pin itself — only a
    # boolean — so the serializer is safe to surface in the
    # read API. The pin is set via a separate admin action.
    tls_cert_sha256_set = serializers.SerializerMethodField()

    def get_has_ssh_credentials(self, obj):
        return bool(str(obj.ssh_password or '').strip() or str(obj.ssh_key or '').strip())

    def get_mesh_dns_names(self, obj):
        try:
            from apps.deployments.services.mesh_dns import (
                _sanitize_label,
                mesh_dns_domain,
            )
            domain = mesh_dns_domain()
            names: list[str] = []
            if getattr(obj, "is_primary", False):
                names.append(f"master.{domain}")
            node_number = getattr(obj, "node_number", None)
            if node_number:
                names.append(f"grid{node_number}.{domain}")
            label = _sanitize_label(str(getattr(obj, "name", "") or ""))
            if label:
                candidate = f"{label}.{domain}"
                if candidate not in names:
                    names.append(candidate)
            return names
        except Exception:
            logger.debug("mesh_dns_names render failed", exc_info=True)
            return []

    class Meta:
        model = ManagedServer
        fields = [
            "id", "name", "host", "private_ip", "api_url", "ssh_port",
            "ssh_user", "provider_metadata", "is_primary",
            "allow_user_workloads", "status", "last_health_check",
            "server_version", "services_count", "created_at",
            "provision_status", "provision_logs", "role", "wg_address",
            "has_ssh_credentials", "is_lite_agent", "node_type",
            "node_components",
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
            "node_number", "node_domain", "mesh_dns_names",
        ]
        read_only_fields = [
            "id", "status", "last_health_check", "server_version",
            "services_count", "created_at", "provision_status",
            "role", "wg_address", "has_ssh_credentials", "is_lite_agent",
            "node_type", "node_components",
            "agent_ready", "last_agent_heartbeat_at", "agent_runtime_info",
            "tls_cert_sha256_set",
            "node_number", "node_domain", "mesh_dns_names",
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
            "ssh_key_passphrase", "ssh_port", "is_primary",
            "allow_user_workloads",
            "provider_metadata", "is_lite_agent", "node_type",
            "node_certificate",
        ]
        extra_kwargs = {
            "api_token": {"write_only": True, "required": False},
            "gateway_secret": {"write_only": True, "required": False},
            "ssh_key": {"write_only": True, "required": False, "trim_whitespace": False},
            "ssh_key_passphrase": {"write_only": True, "required": False},
            "ssh_password": {"write_only": True, "required": False},
            "provider_metadata": {"required": False},
        }

    def validate(self, data):
        is_lite = bool(data.get("is_lite_agent", False))
        node_type = (data.get("node_type") or "").strip()
        # Normalize: flag is the driver for install mode; node_type must agree.
        # Connect-existing cannot set arbitrary types — derive when absent.
        if not node_type:
            node_type = (
                ManagedServer.NodeType.AGENT_LITE if is_lite
                else ManagedServer.NodeType.NODE
            )
            data["node_type"] = node_type
        elif node_type == ManagedServer.NodeType.AGENT_LITE and not is_lite:
            is_lite = True
            data["is_lite_agent"] = True
        elif node_type in (ManagedServer.NodeType.NODE, ManagedServer.NodeType.MEDIA) and is_lite:
            raise serializers.ValidationError(
                {"is_lite_agent": "Contradictory flags: node/media rows cannot be lite agents."}
            )
        if is_lite:
            cert = (data.get("node_certificate") or "").strip()
            if not cert:
                raise serializers.ValidationError(
                    {"node_certificate": "node_certificate is required when is_lite_agent=True."}
                )
        if (req := self.context.get("request")) is not None:
            user = getattr(req, "user", None)
            if data.get("is_primary") and not getattr(user, "is_superuser", False):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only superusers can register a primary server.")
            if (is_lite or node_type in (ManagedServer.NodeType.AGENT_LITE, ManagedServer.NodeType.MEDIA)) and not (
                getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
            ):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only staff or superusers can register lite-agent or media nodes.")
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
        req = self.context.get("request")
        req_user = getattr(req, "user", None) if req is not None else None
        raw_primary = self.initial_data.get("is_primary", False)
        if isinstance(raw_primary, str):
            wants_primary = raw_primary.strip().lower() in ("true", "1", "yes", "t", "on")
        else:
            wants_primary = bool(raw_primary)
        is_primary = bool(wants_primary and req is not None and getattr(req_user, "is_superuser", False))
        if not is_primary:
            try:
                import ipaddress as _ip
                ip = _ip.ip_address(value)
                # RFC1918 (private) hosts are allowed for non-primary
                # servers (VPC / VPN / WireGuard fleets) — matches the
                # pre_save signal in signals/validation.py.
                if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                    raise serializers.ValidationError(
                        f"Host {value} is in a forbidden range for non-primary servers."
                    )
            except ValueError:
                pass  # Hostname — allowed for non-primary
        if value.lower() == "localhost" and not is_primary:
            raise serializers.ValidationError("'localhost' is not allowed as a non-primary host.")
        _assert_public_node_target(self, "host", value)
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
                # SECURITY: reject loopback / link-local / multicast /
                # reserved / unspecified (classic SSRF targets, including
                # the 169.254.169.254 cloud-metadata address). RFC1918
                # private addresses are ALLOWED: WireGuard mesh
                # (10.100.x.x) and VPC private-IP fleets are supported
                # topologies that must be able to register.
                from django.conf import settings
                allow_local = getattr(settings, 'ALLOW_LOCAL_NODES', False)
                if not allow_local:
                    if (ip.is_loopback or ip.is_link_local
                            or ip.is_multicast or ip.is_reserved
                            or ip.is_unspecified):
                        raise serializers.ValidationError(
                            f"api_url IP {ip} is not a public address "
                            f"(loopback / link-local / multicast / reserved)."
                        )
            except ValueError:
                pass  # hostname — allowed
        _assert_public_node_target(self, "api_url", value)
        return value


class ManagedServerProvisionSerializer(serializers.ModelSerializer):
    """For 'Provision New' mode — user provides SSH credentials.

    ``ssh_auth_method`` supports three modes:
    - ``password`` — user provides the SSH password (bootstrap credential).
    - ``key`` — user pastes their own PEM private key.
    - ``generated`` — the platform generates an Ed25519 keypair at
      provisioning time; the private key is stored encrypted and the public
      key is returned to the user, who installs it on the target host.
    """
    ssh_auth_method = serializers.ChoiceField(
        choices=["password", "key", "generated"], write_only=True, required=False, default="password"
    )
    node_certificate = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )
    ssh_key_passphrase = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "ssh_port", "ssh_user",
            "ssh_password", "ssh_key", "ssh_key_passphrase",
            "ssh_auth_method",
            "is_primary", "allow_user_workloads", "is_lite_agent",
            "node_type", "node_components",
            "node_certificate",
        ]
        extra_kwargs = {
            "ssh_password": {"write_only": True},
            "ssh_key": {"write_only": True, "trim_whitespace": False},
        }

    def validate_host(self, value):
        """Provisioning SSHes to this host from the backend container —
        a private/metadata target would be a connect oracle. Non-admin
        users must point at a public address; staff register mesh/VPC
        nodes by design."""
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Host is required.")
        _assert_public_node_target(self, "host", value)
        return value

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
        if method == "generated":
            # The keypair is generated server-side during provision_new; no
            # credentials are required from the user. Never store a
            # user-supplied key in this mode — the generated one replaces it.
            data.pop("ssh_key", None)
            data.pop("ssh_password", None)
            data.pop("ssh_key_passphrase", None)
        elif method == "key" and not (data.get("ssh_key_passphrase") or "").strip():
            data.pop("ssh_key_passphrase", None)
        # If provisioning via SSH, we don't require the certificate upfront.
        # The provisioner script will automatically fetch it from the remote node
        # once the lite agent is installed.
        if data.get("node_type") == "node" and data.get("is_lite_agent"):
            raise serializers.ValidationError(
                {"is_lite_agent": "Contradictory flags: use node_type='agent-lite' for lite agents, or set is_lite_agent to false."}
            )
        if data.get("node_type") == "media" and data.get("is_lite_agent"):
            raise serializers.ValidationError(
                {"is_lite_agent": "Contradictory flags: media nodes are bare-metal installs and cannot be lite agents."}
            )
        if (req := self.context.get("request")) is not None:
            user = getattr(req, "user", None)
            if data.get("is_primary") and not getattr(user, "is_superuser", False):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only superusers can provision a primary server.")
            is_lite = bool(data.get("is_lite_agent", False))
            node_type = (data.get("node_type") or "").strip()
            if (is_lite or node_type in ("agent-lite", "media")) and not (
                getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
            ):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only staff or superusers can provision lite-agent or media nodes.")
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
    '/api/v1/node',
)


class ServerCheckAllThrottle(UserRateThrottle):
    scope = 'server_check_all'


class ServerProvisionThrottle(UserRateThrottle):
    scope = 'server_provision'

