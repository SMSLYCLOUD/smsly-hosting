"""Initial migration for apps.media.

Creates MediaNodeProfile, MediaRoom, MediaParticipant,
AttestationProfile, and AttestationAuditLog.
"""
import django.db.models.deletion
import encrypted_model_fields.fields
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("deployments", "0001_initial"),
    ]

    operations = [
        # ── MediaNodeProfile ──
        migrations.CreateModel(
            name="MediaNodeProfile",
            fields=[
                (
                    "server",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="media_profile",
                        serialize=False,
                        to="deployments.managedserver",
                    ),
                ),
                ("livekit_api_key", models.CharField(blank=True, default="", max_length=128)),
                ("livekit_api_secret", encrypted_model_fields.fields.EncryptedCharField(blank=True, default="", max_length=256)),
                ("livekit_host", models.CharField(default="127.0.0.1", max_length=255)),
                ("livekit_port", models.PositiveIntegerField(default=7880)),
                ("turn_secret", encrypted_model_fields.fields.EncryptedCharField(blank=True, default="", max_length=128)),
                ("turn_realm", models.CharField(default="smsly.com", max_length=255)),
                ("turn_port_tcp", models.PositiveIntegerField(default=3478)),
                ("turn_port_tls", models.PositiveIntegerField(default=5349)),
                ("max_voice_calls", models.PositiveIntegerField(default=5000)),
                ("max_video_rooms", models.PositiveIntegerField(default=100)),
                ("max_participants", models.PositiveIntegerField(default=2000)),
                ("max_rtp_sessions", models.PositiveIntegerField(default=10000)),
                ("active_calls", models.PositiveIntegerField(default=0)),
                ("active_rooms", models.PositiveIntegerField(default=0)),
                ("active_participants", models.PositiveIntegerField(default=0)),
                ("active_rtp_sessions", models.PositiveIntegerField(default=0)),
                ("capacity_score", models.FloatField(default=1.0)),
                ("cpu_percent", models.FloatField(default=0.0)),
                ("memory_percent", models.FloatField(default=0.0)),
                ("disk_percent", models.FloatField(default=0.0)),
                ("last_telemetry_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Media Node Profile",
            },
        ),
        # ── MediaRoom ──
        migrations.CreateModel(
            name="MediaRoom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_id", models.CharField(max_length=128, unique=True)),
                (
                    "room_type",
                    models.CharField(
                        choices=[("voice", "Voice"), ("video", "Video")],
                        default="video",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("ended", "Ended")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("participant_count", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "node",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rooms",
                        to="media.medianodeprofile",
                    ),
                ),
                (
                    "service",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="media_rooms",
                        to="deployments.service",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        # ── MediaParticipant ──
        migrations.CreateModel(
            name="MediaParticipant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("participant_id", models.CharField(max_length=128)),
                ("joined_at", models.DateTimeField(auto_now_add=True)),
                ("left_at", models.DateTimeField(blank=True, null=True)),
                (
                    "room",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participants",
                        to="media.mediaroom",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="media_participations",
                        to="auth.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-joined_at"],
            },
        ),
        # ── AttestationProfile ──
        migrations.CreateModel(
            name="AttestationProfile",
            fields=[
                (
                    "server",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="attestation_profile",
                        serialize=False,
                        to="deployments.managedserver",
                    ),
                ),
                ("public_key", models.BinaryField(blank=True, null=True)),
                ("public_key_hex", models.CharField(blank=True, default="", max_length=512)),
                (
                    "platform_attester",
                    models.CharField(
                        choices=[
                            ("tpm2", "TPM 2.0"),
                            ("secure_enclave", "Secure Enclave"),
                            ("se050", "NXP SE050"),
                            ("software", "Software Fallback"),
                        ],
                        default="software",
                        max_length=30,
                    ),
                ),
                (
                    "algorithm_suite",
                    models.CharField(
                        choices=[
                            ("hybrid", "Hybrid (Classical + PQC)"),
                            ("classical", "Classical Only"),
                            ("pqc", "Post-Quantum Only"),
                        ],
                        default="hybrid",
                        max_length=20,
                    ),
                ),
                ("is_hardware_backed", models.BooleanField(default=False)),
                (
                    "security_level",
                    models.PositiveSmallIntegerField(
                        default=1,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(5),
                        ],
                    ),
                ),
                ("monotonic_counter", models.BigIntegerField(default=0)),
                ("engine_healthy", models.BooleanField(default=False)),
                ("last_attestation_at", models.DateTimeField(blank=True, null=True)),
                ("stamps_generated_total", models.BigIntegerField(default=0)),
                ("stamps_verified_total", models.BigIntegerField(default=0)),
                ("tamper_detections_total", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Attestation Profile",
            },
        ),
        # ── AttestationAuditLog ──
        migrations.CreateModel(
            name="AttestationAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("stamp_generated", "Stamp Generated"),
                            ("stamp_verified", "Stamp Verified"),
                            ("tamper_detected", "Tamper Detected"),
                            ("key_rotated", "Key Rotated"),
                            ("engine_started", "Engine Started"),
                            ("engine_stopped", "Engine Stopped"),
                            ("counter_advanced", "Counter Advanced"),
                        ],
                        max_length=30,
                    ),
                ),
                ("trust_score", models.FloatField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "server",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attestation_events",
                        to="deployments.managedserver",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
