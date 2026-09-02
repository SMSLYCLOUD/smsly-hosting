"""Serializers for Media Node app."""
from rest_framework import serializers

from ..models import MediaNodeInterest, MediaNodeProfile, MediaParticipant, MediaRoom
from ..models.attestation import AttestationAuditLog, AttestationProfile


class MediaNodeInterestSerializer(serializers.ModelSerializer):
    """Write-only lead capture for the enterprise media node workflow.

    Only ``name`` and ``email`` are required; the rest is optional context
    for the sales follow-up. Status is managed internally.
    """

    class Meta:
        model = MediaNodeInterest
        fields = ["id", "name", "company", "email", "host", "notes", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]

    def validate(self, data):
        data["status"] = MediaNodeInterest.Status.NEW
        return data


class MediaNodeProfileSerializer(serializers.ModelSerializer):
    server_name = serializers.CharField(source="server.name", read_only=True)
    server_host = serializers.CharField(source="server.host", read_only=True)
    server_status = serializers.CharField(source="server.status", read_only=True)
    server_wg_address = serializers.CharField(source="server.wg_address", read_only=True)

    class Meta:
        model = MediaNodeProfile
        fields = [
            "server",
            "server_name",
            "server_host",
            "server_status",
            "server_wg_address",
            "script_repo_url",
            "script_repo_token",
            "livekit_host",
            "livekit_port",
            "turn_realm",
            "turn_port_tcp",
            "turn_port_tls",
            "max_voice_calls",
            "max_video_rooms",
            "max_participants",
            "max_rtp_sessions",
            "active_calls",
            "active_rooms",
            "active_participants",
            "active_rtp_sessions",
            "capacity_score",
            "cpu_percent",
            "memory_percent",
            "disk_percent",
            "last_telemetry_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "active_calls",
            "active_rooms",
            "active_participants",
            "active_rtp_sessions",
            "capacity_score",
            "cpu_percent",
            "memory_percent",
            "disk_percent",
            "last_telemetry_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "script_repo_token": {"write_only": True}
        }


class MediaRoomSerializer(serializers.ModelSerializer):
    node_server = serializers.CharField(source="node.server.host", read_only=True)

    class Meta:
        model = MediaRoom
        fields = [
            "room_id",
            "node",
            "node_server",
            "service",
            "room_type",
            "status",
            "participant_count",
            "metadata",
            "created_at",
            "ended_at",
        ]
        read_only_fields = ["participant_count", "created_at", "ended_at"]


class MediaParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaParticipant
        fields = [
            "room",
            "participant_id",
            "user",
            "joined_at",
            "left_at",
        ]
        read_only_fields = ["joined_at", "left_at"]


class AttestationProfileSerializer(serializers.ModelSerializer):
    server_name = serializers.CharField(source="server.name", read_only=True)

    class Meta:
        model = AttestationProfile
        fields = [
            "server",
            "server_name",
            "platform_attester",
            "algorithm_suite",
            "is_hardware_backed",
            "security_level",
            "monotonic_counter",
            "engine_healthy",
            "last_attestation_at",
            "stamps_generated_total",
            "stamps_verified_total",
            "tamper_detections_total",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "monotonic_counter",
            "engine_healthy",
            "last_attestation_at",
            "stamps_generated_total",
            "stamps_verified_total",
            "tamper_detections_total",
            "created_at",
            "updated_at",
        ]


class AttestationAuditLogSerializer(serializers.ModelSerializer):
    server_name = serializers.CharField(source="server.name", read_only=True)

    class Meta:
        model = AttestationAuditLog
        fields = [
            "id",
            "server",
            "server_name",
            "event_type",
            "trust_score",
            "metadata",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
