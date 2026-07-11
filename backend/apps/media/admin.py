"""Django admin for Media Node app."""
from django.contrib import admin

from .models import MediaNodeProfile, MediaParticipant, MediaRoom
from .models_attestation import AttestationAuditLog, AttestationProfile


@admin.register(MediaNodeProfile)
class MediaNodeProfileAdmin(admin.ModelAdmin):
    list_display = [
        "server",
        "capacity_score",
        "active_calls",
        "active_rooms",
        "cpu_percent",
        "last_telemetry_at",
    ]
    readonly_fields = [
        "active_calls",
        "active_rooms",
        "active_participants",
        "active_rtp_sessions",
        "capacity_score",
        "cpu_percent",
        "memory_percent",
        "disk_percent",
        "last_telemetry_at",
    ]


@admin.register(MediaRoom)
class MediaRoomAdmin(admin.ModelAdmin):
    list_display = ["room_id", "node", "room_type", "status", "participant_count", "created_at"]
    list_filter = ["room_type", "status"]


@admin.register(MediaParticipant)
class MediaParticipantAdmin(admin.ModelAdmin):
    list_display = ["participant_id", "room", "user", "joined_at", "left_at"]


@admin.register(AttestationProfile)
class AttestationProfileAdmin(admin.ModelAdmin):
    list_display = [
        "server",
        "platform_attester",
        "engine_healthy",
        "monotonic_counter",
        "last_attestation_at",
    ]
    readonly_fields = [
        "monotonic_counter",
        "engine_healthy",
        "last_attestation_at",
        "stamps_generated_total",
        "stamps_verified_total",
        "tamper_detections_total",
    ]


@admin.register(AttestationAuditLog)
class AttestationAuditLogAdmin(admin.ModelAdmin):
    list_display = ["server", "event_type", "trust_score", "created_at"]
    list_filter = ["event_type"]
    readonly_fields = ["created_at"]
