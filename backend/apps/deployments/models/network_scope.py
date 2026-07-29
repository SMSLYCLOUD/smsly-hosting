"""
Scoped Docker network isolation.

Attaches network configuration (name, driver, isolation mode) to any
hierarchical entity (Organization, Team, or Project) via GenericForeignKey.

Resolution chain (walks up):
    Project → Team → Organization → PlatformConfig (global fallback: smsly-net)

Mirrors the ``ScopedRegistry`` pattern exactly. When a network is scoped
with ``isolated=True``, containers under that scope are placed on a
dedicated Docker bridge network, preventing cross-scope communication
at the network layer.

Usage::

    # Get the effective network for a project
    network_name = ScopedNetwork.resolve_network_name(project)
    # → 'smsly-net' (global) or 'smsly-net-proj-<uuid>' (isolated)

    # Resolve full network config
    cfg = ScopedNetwork.resolve_network_config(project)
    # → {'name': '...', 'driver': 'bridge', 'isolated': True, ...}
"""

import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ScopedNetwork(models.Model):
    """
    Docker network configuration attachable to a single scoped entity.

    Only one ScopedNetwork per entity is allowed (unique_together).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Generic FK — attaches to Organization, Team, or Project ──────────
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        limit_choices_to={"model__in": ["organization", "team", "project"]},
    )
    object_id = models.UUIDField()
    scope = GenericForeignKey("content_type", "object_id")

    # ── Network configuration ────────────────────────────────────────────
    network_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Docker network name. Leave blank to inherit from parent scope.",
    )
    driver = models.CharField(
        max_length=50,
        default="bridge",
        help_text="Docker network driver (bridge, overlay, macvlan, etc.)",
    )
    isolated = models.BooleanField(
        default=False,
        help_text="If True, containers under this scope get a dedicated isolated network "
                  "that prevents cross-scope communication",
    )
    internal = models.BooleanField(
        default=False,
        help_text="If True, the network has no external connectivity (--internal)",
    )
    enable_ipv6 = models.BooleanField(
        default=False,
        help_text="Enable IPv6 on the network",
    )
    subnet = models.CharField(
        max_length=43,
        blank=True,
        default="",
        help_text="Custom subnet CIDR (auto-assigned if blank)",
    )

    # ── Ingress policy ───────────────────────────────────────────────────
    allow_public_traefik = models.BooleanField(
        default=True,
        help_text="If False, containers on this network are NOT exposed via Traefik "
                  "(internal-only, even for services with public domains)",
    )
    allowed_egress_networks = models.JSONField(
        default=list,
        blank=True,
        help_text="List of CIDR ranges containers on this network can reach. "
                  "Empty = unrestricted egress.",
    )

    # ── State ────────────────────────────────────────────────────────────
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Scoped Network"
        verbose_name_plural = "Scoped Networks"
        unique_together = [("content_type", "object_id")]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        scope_label = str(self.scope) if self.scope else "(orphaned)"
        label = self.network_name or "(inherited)"
        iso = " [isolated]" if self.isolated else ""
        return f"{scope_label}: {label}{iso}"

    # ── Hierarchical resolution helpers ──────────────────────────────────

    @classmethod
    def get_for_object(cls, obj):
        """Return the ScopedNetwork for *obj*, walking up the hierarchy.

        Walks: Project → Team → Organization → None
        """
        if obj is None:
            return None

        ct = ContentType.objects.get_for_model(obj)
        direct = (
            cls.objects.filter(content_type=ct, object_id=obj.id, is_active=True)
            .select_related("content_type")
            .first()
        )
        if direct:
            return direct

        from apps.teams.models import Team

        from .core import Project

        if isinstance(obj, Project):
            if obj.team_id:
                return cls.get_for_object(obj.team)
            return None
        if isinstance(obj, Team):
            if obj.organization_id:
                return cls.get_for_object(obj.organization)
            return None
        return None

    @classmethod
    def resolve_network_name(cls, obj) -> str:
        """Resolve the effective Docker network name for *obj*."""
        network = cls.get_for_object(obj)
        if network:
            if network.network_name:
                return network.network_name
            if network.isolated:
                scope_id = str(network.object_id).replace("-", "")[:8]
                return f"smsly-net-{scope_id}"
        return "smsly-net"

    @classmethod
    def resolve_network_config(cls, obj) -> dict:
        """Return the full network config dict for container creation.

        Returns::

            {
                'name': 'smsly-net',
                'driver': 'bridge',
                'isolated': False,
                'internal': False,
                'enable_ipv6': False,
                'subnet': '',
                'allow_public_traefik': True,
                'allowed_egress_networks': [],
            }
        """
        network = cls.get_for_object(obj)
        if not network:
            return {
                "name": "smsly-net",
                "driver": "bridge",
                "isolated": False,
                "internal": False,
                "enable_ipv6": False,
                "subnet": "",
                "allow_public_traefik": True,
                "allowed_egress_networks": [],
            }

        name = network.network_name
        if not name and network.isolated:
            scope_id = str(network.object_id).replace("-", "")[:8]
            name = f"smsly-net-{scope_id}"
        if not name:
            name = "smsly-net"

        return {
            "name": name,
            "driver": network.driver or "bridge",
            "isolated": network.isolated,
            "internal": network.internal,
            "enable_ipv6": network.enable_ipv6,
            "subnet": network.subnet or "",
            "allow_public_traefik": network.allow_public_traefik,
            "allowed_egress_networks": list(network.allowed_egress_networks or []),
        }

    @classmethod
    def _get_scope_chain(cls, obj) -> list:
        """Return [project, team, organization] walking up, skipping None."""
        from apps.organizations.models import Organization
        from apps.teams.models import Team

        from .core import Project

        chain: list = []
        if isinstance(obj, Project):
            chain.append(obj)
            if obj.team_id:
                chain.append(obj.team)
                if obj.team.organization_id:
                    chain.append(obj.team.organization)
        elif isinstance(obj, Team):
            chain.append(obj)
            if obj.organization_id:
                chain.append(obj.organization)
        elif isinstance(obj, Organization):
            chain.append(obj)
        return chain
