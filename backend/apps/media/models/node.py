"""Media node models for SMSLY voice/video infrastructure."""
import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class MediaNodeProfile(models.Model):
    """Profile for a baremetal media node -- 1:1 with ManagedServer."""

    server = models.OneToOneField(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="media_profile",
    )

    # -- LiveKit credentials --
    livekit_api_key = models.CharField(max_length=128, blank=True, default="")  # type: ignore[var-annotated]
    livekit_api_secret = EncryptedCharField(max_length=256, blank=True, default="")
    livekit_host = models.CharField(max_length=255, default="127.0.0.1")  # type: ignore[var-annotated]
    livekit_port = models.PositiveIntegerField(default=7880)  # type: ignore[var-annotated]

    # -- TURN configuration --
    turn_secret = EncryptedCharField(max_length=128, blank=True, default="")
    turn_realm = models.CharField(max_length=255, default="smsly.com")  # type: ignore[var-annotated]
    turn_port_tcp = models.PositiveIntegerField(default=3478)  # type: ignore[var-annotated]
    turn_port_tls = models.PositiveIntegerField(default=5349)  # type: ignore[var-annotated]

    # -- Capacity limits --
    max_voice_calls = models.PositiveIntegerField(default=5000)  # type: ignore[var-annotated]
    max_video_rooms = models.PositiveIntegerField(default=100)  # type: ignore[var-annotated]
    max_participants = models.PositiveIntegerField(default=2000)  # type: ignore[var-annotated]
    max_rtp_sessions = models.PositiveIntegerField(default=10000)  # type: ignore[var-annotated]

    # -- Live metrics (updated via WebSocket push) --
    active_calls = models.PositiveIntegerField(default=0)  # type: ignore[var-annotated]
    active_rooms = models.PositiveIntegerField(default=0)  # type: ignore[var-annotated]
    active_participants = models.PositiveIntegerField(default=0)  # type: ignore[var-annotated]
    active_rtp_sessions = models.PositiveIntegerField(default=0)  # type: ignore[var-annotated]
    capacity_score = models.FloatField(default=1.0)  # type: ignore[var-annotated]

    # -- Telemetry --
    cpu_percent = models.FloatField(default=0.0)  # type: ignore[var-annotated]
    memory_percent = models.FloatField(default=0.0)  # type: ignore[var-annotated]
    disk_percent = models.FloatField(default=0.0)  # type: ignore[var-annotated]

    last_telemetry_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Media Node Profile"

    def __str__(self):
        return f"MediaProfile({self.server_id})"


class MediaRoom(models.Model):
    """A voice/video room hosted on a media node."""

    class RoomType(models.TextChoices):
        VOICE = "voice", "Voice"
        VIDEO = "video", "Video"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    room_id = models.CharField(max_length=128, unique=True)  # type: ignore[var-annotated]
    node = models.ForeignKey(
        MediaNodeProfile,
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    service = models.ForeignKey(
        "deployments.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="media_rooms",
    )
    room_type = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=RoomType.choices,
        default=RoomType.VIDEO,
    )
    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    participant_count = models.PositiveIntegerField(default=0)  # type: ignore[var-annotated]
    metadata = models.JSONField(default=dict, blank=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    ended_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Room({self.room_id})"


class MediaParticipant(models.Model):
    """A participant in a media room."""

    room = models.ForeignKey(
        MediaRoom,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    participant_id = models.CharField(max_length=128)  # type: ignore[var-annotated]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="media_participations",
    )
    joined_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    left_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ["-joined_at"]

    def __str__(self):
        return f"Participant({self.participant_id})"
