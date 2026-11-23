"""Unified OCSF v1.1 Telemetry, Finding Lifecycle, SBOM/SLSA, Forensics, and GRC Models."""
from __future__ import annotations

import hashlib
import uuid

from django.core.cache import cache
from django.db import models
from django.utils import timezone


class SecurityPolicy(models.Model):
    """Singleton platform security gate & SLA configuration."""

    singleton_key = models.CharField(max_length=32, default="default", unique=True)
    aspm_enabled = models.BooleanField(default=True)
    aspm_block_on_secrets = models.BooleanField(
        default=False,
        help_text="Fail deployment when high-confidence hardcoded secrets are detected in source.",
    )
    aspm_block_on_critical_sast = models.BooleanField(
        default=False,
        help_text="Fail deployment when CRITICAL SAST issues are detected in source.",
    )
    aspm_block_on_critical_iac = models.BooleanField(
        default=False,
        help_text="Fail deployment when CRITICAL IaC/Dockerfile issues are detected.",
    )
    sbom_enabled = models.BooleanField(default=True)
    slsa_provenance_enabled = models.BooleanField(default=True)
    dast_on_deploy_enabled = models.BooleanField(default=True)
    ai_prompt_firewall_enabled = models.BooleanField(default=True)
    auto_quarantine_on_critical_runtime = models.BooleanField(default=False)

    sla_critical_hours = models.PositiveIntegerField(default=24)
    sla_high_days = models.PositiveIntegerField(default=7)
    sla_medium_days = models.PositiveIntegerField(default=30)

    updated_at = models.DateTimeField(auto_now=True)

    _CACHE_KEY = "smsly:security:policy:default"

    @classmethod
    def clear_cache(cls) -> None:
        try:
            cache.delete(cls._CACHE_KEY)
        except Exception:
            pass

    @classmethod
    def load(cls) -> "SecurityPolicy":
        from django.conf import settings

        use_cache = not bool(getattr(settings, "IS_TESTING", False))
        if use_cache:
            cached = cache.get(cls._CACHE_KEY)
            if cached is not None:
                return cached
        obj, _ = cls.objects.get_or_create(singleton_key="default")
        if use_cache:
            try:
                cache.set(cls._CACHE_KEY, obj, timeout=60)
            except Exception:
                pass
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.clear_cache()

    def __str__(self) -> str:
        return f"SecurityPolicy({self.singleton_key})"


class NormalizedSecurityEvent(models.Model):
    """OCSF v1.1 normalized telemetry lake spanning Edge, Runtime, ASPM, CNAPP & XDR."""

    class Severity(models.TextChoices):
        INFO = "INFO", "Informational"
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=32, db_index=True)
    source_uid = models.CharField(max_length=128)
    category_uid = models.IntegerField(default=2)
    category_name = models.CharField(max_length=64, default="Findings")
    class_uid = models.IntegerField(default=2001)
    class_name = models.CharField(max_length=64, default="Security Finding")
    activity_name = models.CharField(max_length=64, default="Detect")
    severity_id = models.SmallIntegerField(default=1)
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.INFO, db_index=True
    )

    title = models.CharField(max_length=255)
    details = models.TextField(blank=True, default="")
    target = models.CharField(max_length=255, blank=True, default="", db_index=True)
    actor_ip = models.CharField(max_length=64, blank=True, default="")
    actor_user = models.CharField(max_length=150, blank=True, default="")

    mitre_tactic = models.CharField(max_length=64, blank=True, default="", db_index=True)
    mitre_technique = models.CharField(max_length=32, blank=True, default="", db_index=True)
    mitre_technique_name = models.CharField(max_length=128, blank=True, default="")

    service = models.ForeignKey(
        "deployments.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="normalized_security_events",
    )
    deployment = models.ForeignKey(
        "deployments.Deployment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="normalized_security_events",
    )
    server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="normalized_security_events",
    )

    raw_ocsf = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_uid"],
                name="sec_ocsf_source_uid_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "-occurred_at"], name="sec_evt_src_ts_idx"),
            models.Index(fields=["mitre_technique", "-occurred_at"], name="sec_evt_mitre_idx"),
            models.Index(fields=["service", "-occurred_at"], name="sec_evt_svc_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.source}: {self.title}"


class SecurityFinding(models.Model):
    """Deduplicated security finding with Risk Fusion scoring & SLA lifecycle."""

    class Pillar(models.TextChoices):
        CORE = "CORE", "Aeon Core"
        ASPM = "ASPM", "ASPM"
        CNAPP = "CNAPP", "CNAPP"
        XDR = "XDR", "XDR"

    class Category(models.TextChoices):
        SAST = "SAST", "SAST Code Issue"
        SCA = "SCA", "SCA Dependency CVE"
        SECRETS = "SECRETS", "Exposed Secret"
        IMAGE_CVE = "IMAGE_CVE", "Container Image CVE"
        IAC = "IAC", "IaC / Container Misconfiguration"
        DAST = "DAST", "DAST Web Exposure"
        CSPM = "CSPM", "Cloud / Host Posture"
        KSPM = "KSPM", "Container / Orchestrator Posture"
        CIEM = "CIEM", "Identity Entitlement"
        DSPM = "DSPM", "Data Security Posture"
        AI_SPM = "AI_SPM", "AI Security Posture"
        RUNTIME = "RUNTIME", "Runtime / eBPF Threat"
        IDENTITY = "IDENTITY", "Identity / UEBA Anomaly"
        EDGE = "EDGE", "Edge / Route / DNS Integrity"

    class Severity(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"
        INFO = "INFO", "Informational"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        RESOLVED = "RESOLVED", "Resolved"
        FALSE_POSITIVE = "FALSE_POSITIVE", "False Positive"
        RISK_ACCEPTED = "RISK_ACCEPTED", "Risk Accepted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=64, unique=True)
    pillar = models.CharField(max_length=16, choices=Pillar.choices, default=Pillar.CORE, db_index=True)
    category = models.CharField(max_length=24, choices=Category.choices, db_index=True)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.MEDIUM, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)

    # Risk Fusion factors
    risk_score = models.SmallIntegerField(default=50, db_index=True)
    epss_score = models.FloatField(default=0.0)
    internet_exposed = models.BooleanField(default=False)
    data_sensitive = models.BooleanField(default=False)
    privileged = models.BooleanField(default=False)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    remediation = models.TextField(blank=True, default="")

    package_name = models.CharField(max_length=150, blank=True, default="")
    installed_version = models.CharField(max_length=64, blank=True, default="")
    fixed_version = models.CharField(max_length=64, blank=True, default="")
    file_path = models.CharField(max_length=255, blank=True, default="")
    line_number = models.PositiveIntegerField(null=True, blank=True)
    cve_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    cwe_id = models.CharField(max_length=32, blank=True, default="")

    mitre_tactic = models.CharField(max_length=64, blank=True, default="")
    mitre_technique = models.CharField(max_length=32, blank=True, default="")
    mitre_technique_name = models.CharField(max_length=128, blank=True, default="")

    service = models.ForeignKey(
        "deployments.Service",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="security_findings",
    )
    deployment = models.ForeignKey(
        "deployments.Deployment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_findings",
    )
    server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_findings",
    )

    evidence = models.JSONField(default=dict, blank=True)
    sla_due_at = models.DateTimeField(null=True, blank=True)
    suppressed_until = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-risk_score", "-last_seen_at"]
        indexes = [
            models.Index(fields=["status", "-risk_score"], name="sec_fnd_st_risk_idx"),
            models.Index(fields=["pillar", "status"], name="sec_fnd_pil_st_idx"),
            models.Index(fields=["service", "status"], name="sec_fnd_svc_st_idx"),
        ]

    @staticmethod
    def build_fingerprint(*parts: object) -> str:
        raw = "|".join(str(p or "").strip() for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def __str__(self) -> str:
        return f"[{self.severity} {self.risk_score}] {self.category}: {self.title}"


class SBOMArtifact(models.Model):
    """CycloneDX / SPDX SBOM & SLSA v1.0 Build Provenance stored per deployment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deployment = models.OneToOneField(
        "deployments.Deployment",
        on_delete=models.CASCADE,
        related_name="sbom_artifact",
    )
    service = models.ForeignKey(
        "deployments.Service",
        on_delete=models.CASCADE,
        related_name="sbom_artifacts",
    )
    sbom_format = models.CharField(max_length=16, default="cyclonedx")
    spec_version = models.CharField(max_length=32, default="1.5")
    image_ref = models.CharField(max_length=255, blank=True, default="")
    image_digest = models.CharField(max_length=128, blank=True, default="")
    package_count = models.PositiveIntegerField(default=0)
    packages = models.JSONField(default=list, blank=True)
    sbom_document = models.JSONField(default=dict, blank=True)
    slsa_provenance = models.JSONField(default=dict, blank=True)
    provenance_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["service", "-created_at"], name="sec_sbom_svc_idx"),
        ]

    def __str__(self) -> str:
        return f"SBOM({self.service.name}@{self.deployment_id}, pkgs={self.package_count})"


class ComplianceControlResult(models.Model):
    """Live GRC compliance control evaluation backed by real infrastructure telemetry."""

    class Framework(models.TextChoices):
        SOC2 = "SOC2", "SOC 2 Type II"
        ISO27001 = "ISO27001", "ISO 27001:2022"
        NDPR = "NDPR", "NDPR (Nigeria Data Protection)"
        PCIDSS = "PCIDSS", "PCI-DSS v4.0"

    class Status(models.TextChoices):
        PASS = "PASS", "Pass"
        PARTIAL = "PARTIAL", "Partial"
        FAIL = "FAIL", "Fail"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    framework = models.CharField(max_length=24, choices=Framework.choices, db_index=True)
    control_id = models.CharField(max_length=32)
    control_name = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PARTIAL)
    score_pct = models.PositiveSmallIntegerField(default=0)
    evidence_summary = models.TextField(blank=True, default="")
    evidence_details = models.JSONField(default=dict, blank=True)
    remediation = models.TextField(blank=True, default="")
    evaluated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["framework", "control_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["framework", "control_id"],
                name="sec_grc_fw_ctrl_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.framework}] {self.control_id} ({self.status})"


class ForensicSnapshot(models.Model):
    """Immutable SHA-256-signed DFIR bundle captured before containment or teardown."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        "deployments.Service",
        on_delete=models.CASCADE,
        related_name="forensic_snapshots",
    )
    deployment = models.ForeignKey(
        "deployments.Deployment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forensic_snapshots",
    )
    container_id = models.CharField(max_length=128, blank=True, default="")
    trigger = models.CharField(max_length=64, default="manual")
    captured_by = models.CharField(max_length=150, default="system")
    sha256_digest = models.CharField(max_length=64, blank=True, default="")
    bundle = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"DFIR({self.service.name}, {self.trigger}, {self.sha256_digest[:8]})"
