"""Attestation models — local cache of edge attestation state.

The authoritative trust chain lives in SMSLYCLOUD (Transaction Chain).
These models are a local PaaS cache for dashboard display and fast lookups.
"""
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class AttestationProfile(models.Model):
    """Local copy of an edge node's attestation key and status."""

    class PlatformAttester(models.TextChoices):
        TPM2 = "tpm2", "TPM 2.0"
        SECURE_ENCLAVE = "secure_enclave", "Secure Enclave"
        SE050 = "se050", "NXP SE050"
        SOFTWARE = "software", "Software Fallback"

    class AlgorithmSuite(models.TextChoices):
        HYBRID = "hybrid", "Hybrid (Classical + PQC)"
        CLASSICAL = "classical", "Classical Only"
        PQC = "pqc", "Post-Quantum Only"

    server = models.OneToOneField(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="attestation_profile",
    )
    public_key = models.BinaryField(blank=True, null=True)  # type: ignore[var-annotated]
    public_key_hex = models.CharField(max_length=512, blank=True, default="")  # type: ignore[var-annotated]
    platform_attester = models.CharField(  # type: ignore[var-annotated]
        max_length=30,
        choices=PlatformAttester.choices,
        default=PlatformAttester.SOFTWARE,
    )
    algorithm_suite = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=AlgorithmSuite.choices,
        default=AlgorithmSuite.HYBRID,
    )
    is_hardware_backed = models.BooleanField(default=False)  # type: ignore[var-annotated]
    security_level = models.PositiveSmallIntegerField(  # type: ignore[var-annotated]
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1 = software, 5 = dedicated secure element",
    )
    monotonic_counter = models.BigIntegerField(default=0)  # type: ignore[var-annotated]
    engine_healthy = models.BooleanField(default=False)  # type: ignore[var-annotated]
    last_attestation_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    stamps_generated_total = models.BigIntegerField(default=0)  # type: ignore[var-annotated]
    stamps_verified_total = models.BigIntegerField(default=0)  # type: ignore[var-annotated]
    tamper_detections_total = models.BigIntegerField(default=0)  # type: ignore[var-annotated]

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Attestation Profile"

    def __str__(self):
        return f"AttestationProfile({self.server_id})"


class AttestationAuditLog(models.Model):
    """Local audit log for attestation events. Forwarded to SMSLYCLOUD Chain."""

    class EventType(models.TextChoices):
        STAMP_GENERATED = "stamp_generated", "Stamp Generated"
        STAMP_VERIFIED = "stamp_verified", "Stamp Verified"
        TAMPER_DETECTED = "tamper_detected", "Tamper Detected"
        KEY_ROTATED = "key_rotated", "Key Rotated"
        ENGINE_STARTED = "engine_started", "Engine Started"
        ENGINE_STOPPED = "engine_stopped", "Engine Stopped"
        COUNTER_ADVANCED = "counter_advanced", "Counter Advanced"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        related_name="attestation_events",
    )
    event_type = models.CharField(  # type: ignore[var-annotated]
        max_length=30,
        choices=EventType.choices,
    )
    trust_score = models.FloatField(null=True, blank=True)  # type: ignore[var-annotated]
    metadata = models.JSONField(default=dict, blank=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AttestationEvent({self.event_type}, {self.server_id})"
