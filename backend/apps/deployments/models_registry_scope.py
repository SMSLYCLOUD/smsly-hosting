"""
Scoped container registry configuration.

Attaches registry connection details (URL + credentials) to any hierarchical
entity (Organization, Team, or Project) via a GenericForeignKey.

Resolution chain (walks up):
    Project → Team → Organization → PlatformConfig (global fallback)

This is the reference implementation for the "scope-able configuration" pattern.
Future features with attack surface (webhook allowlists, IP allowlists, SSH key
scoping, preview environment limits) should follow the same GenericForeignKey
pattern.
"""

import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class ScopedRegistry(models.Model):
    """
    Container registry configuration attachable to a single scoped entity.

    Only one ScopedRegistry per entity is allowed (enforced by unique_together).
    To give a different registry to different projects, create separate records.

    Usage::

        # Get the effective registry for a project (walks hierarchy)
        info = ScopedRegistry.resolve_registry_credentials(project)
        # → {'url': '...', 'username': '...', 'password': '...'}

        # Check what registries a project can pull from
        hosts = ScopedRegistry.resolve_allowed_hosts(project)
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

    # ── Registry connection details ──────────────────────────────────────
    registry_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="e.g. registry.example.com:5000 or 127.0.0.1:5000",
    )
    username = EncryptedCharField(
        max_length=255, blank=True, default="",
    )
    password = EncryptedCharField(
        max_length=512, blank=True, default="",
    )
    is_internal = models.BooleanField(
        default=False,
        help_text="If True, this is a platform-internal registry reachable via the mesh VPN",
    )

    # ── Per-scope allowlist overrides ────────────────────────────────────
    # These hosts are appended to the platform-wide ALLOWED_IMAGE_REGISTRY_HOSTS
    # for all services under this scope.
    allowed_registry_hosts = models.JSONField(
        default=list,
        blank=True,
        help_text="Additional registry hosts allowed for this scope "
                  "(appended to platform-wide allowlist)",
    )

    # ── State ────────────────────────────────────────────────────────────
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Scoped Registry"
        verbose_name_plural = "Scoped Registries"
        unique_together = [("content_type", "object_id")]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        scope_label = str(self.scope) if self.scope else "(orphaned)"
        return f"{scope_label}: {self.registry_url or '(inherited)'}"

    # ── Hierarchical resolution helpers ──────────────────────────────────

    @classmethod
    def get_for_object(cls, obj):
        """Return the ScopedRegistry for *obj*, walking up the hierarchy.

        Walks: Project → Team → Organization → None

        Returns the closest ancestor that has a ScopedRegistry, or *None*
        if nothing is configured in the chain.
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

        # Walk up
        from apps.organizations.models import Organization
        from apps.teams.models import Team

        # Need to import Project lazily to avoid circular import
        from .models_core import Project

        if isinstance(obj, Project):
            if obj.team_id:
                return cls.get_for_object(obj.team)
            return None
        if isinstance(obj, Team):
            if obj.organization_id:
                return cls.get_for_object(obj.organization)
            return None
        if isinstance(obj, Organization):
            return None  # Top of chain
        return None

    @classmethod
    def resolve_registry_url(cls, obj) -> str | None:
        """Resolve the effective registry URL for *obj*.

        Walks the scope chain.  Falls back to ``PlatformConfig.get_config_value``
        when nothing is configured in the chain.
        """
        registry = cls.get_for_object(obj)
        if registry and registry.registry_url:
            return registry.registry_url
        # Fall back to PlatformConfig global
        from .models_core import PlatformConfig

        return PlatformConfig.get_config_value(
            "container_registry_url"
        ) or getattr(
            __import__("django.conf", fromlist=["settings"]).settings,
            "CONTAINER_REGISTRY_URL",
            None,
        )

    @classmethod
    def resolve_registry_credentials(cls, obj) -> dict:
        """Return ``{url, username, password}`` for the highest-priority scope.

        Walks: Deployment override → Project → Team → Organization → PlatformConfig

        If a ScopedRegistry exists but has no ``registry_url``, its username and
        password are still used — only the URL falls back to PlatformConfig.
        """
        registry = cls.get_for_object(obj)

        # Fall back to PlatformConfig global for any missing fields
        from .models_core import PlatformConfig

        default_url = PlatformConfig.get_config_value(
            "container_registry_url"
        ) or getattr(
            __import__("django.conf", fromlist=["settings"]).settings,
            "CONTAINER_REGISTRY_URL",
            "registry:5000",
        )
        default_user = PlatformConfig.get_config_value("registry_user") or "smsly-registry"
        default_pass = PlatformConfig.get_config_value("registry_password") or ""

        if registry:
            return {
                "url": registry.registry_url or default_url,
                "username": registry.username or default_user,
                "password": registry.password if registry.password else default_pass,
            }

        return {
            "url": default_url,
            "username": default_user,
            "password": default_pass,
        }

    @classmethod
    def resolve_allowed_hosts(cls, obj) -> list[str]:
        """Resolve the effective registry allowlist for *obj*.

        Returns the platform-wide defaults **plus** any per-scope extensions
        from every level in the chain.  Closest wins for duplicates.
        """
        from .registry_validation import ALLOWED_IMAGE_REGISTRY_HOSTS

        hosts = list(ALLOWED_IMAGE_REGISTRY_HOSTS)

        chain = cls._get_scope_chain(obj)
        for scope_obj in chain:
            reg = cls.get_for_object(scope_obj)
            if reg and reg.allowed_registry_hosts:
                # Append unique hosts
                for h in reg.allowed_registry_hosts:
                    if h not in hosts:
                        hosts.append(h)

        return hosts

    # ── Internal helpers ────────────────────────────────────────────────

    @classmethod
    def _get_scope_chain(cls, obj) -> list:
        """Return ``[project, team, organization]`` walking up, skipping None."""
        from apps.organizations.models import Organization
        from apps.teams.models import Team

        from .models_core import Project

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
