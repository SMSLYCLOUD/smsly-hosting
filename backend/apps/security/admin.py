"""Django Admin registration for SMSLY Aegis Security Engine models."""
from __future__ import annotations

try:
    from django.apps import apps
    from django.contrib import admin

    from .models import (
        ComplianceControlResult,
        ForensicSnapshot,
        NormalizedSecurityEvent,
        SBOMArtifact,
        SecurityFinding,
        SecurityPolicy,
    )

    if apps.is_installed("django.contrib.admin"):

        @admin.register(SecurityPolicy)
        class SecurityPolicyAdmin(admin.ModelAdmin):
            list_display = (
                "singleton_key",
                "aspm_enabled",
                "aspm_block_on_secrets",
                "aspm_block_on_critical_sast",
                "aspm_block_on_critical_iac",
                "sbom_enabled",
                "dast_on_deploy_enabled",
                "ai_prompt_firewall_enabled",
                "auto_quarantine_on_critical_runtime",
                "updated_at",
            )

        @admin.register(SecurityFinding)
        class SecurityFindingAdmin(admin.ModelAdmin):
            list_display = (
                "title",
                "pillar",
                "category",
                "severity",
                "risk_score",
                "status",
                "service",
                "mitre_technique",
                "last_seen_at",
            )
            list_filter = ("pillar", "category", "severity", "status", "internet_exposed")
            search_fields = ("title", "description", "cve_id", "package_name", "file_path")

        @admin.register(NormalizedSecurityEvent)
        class NormalizedSecurityEventAdmin(admin.ModelAdmin):
            list_display = (
                "title",
                "source",
                "severity",
                "mitre_technique",
                "target",
                "actor_ip",
                "service",
                "occurred_at",
            )
            list_filter = ("source", "severity", "mitre_tactic")
            search_fields = ("title", "details", "target", "actor_ip", "actor_user")

        @admin.register(SBOMArtifact)
        class SBOMArtifactAdmin(admin.ModelAdmin):
            list_display = (
                "service",
                "deployment",
                "sbom_format",
                "spec_version",
                "package_count",
                "provenance_verified",
                "created_at",
            )
            list_filter = ("sbom_format", "provenance_verified")
            search_fields = ("service__name", "image_ref", "image_digest")

        @admin.register(ComplianceControlResult)
        class ComplianceControlResultAdmin(admin.ModelAdmin):
            list_display = (
                "framework",
                "control_id",
                "control_name",
                "status",
                "score_pct",
                "evaluated_at",
            )
            list_filter = ("framework", "status")

        @admin.register(ForensicSnapshot)
        class ForensicSnapshotAdmin(admin.ModelAdmin):
            list_display = (
                "service",
                "trigger",
                "captured_by",
                "container_id",
                "sha256_digest",
                "created_at",
            )
            list_filter = ("trigger",)
            search_fields = ("service__name", "container_id", "sha256_digest")
except Exception:
    pass
