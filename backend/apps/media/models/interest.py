"""Lead-capture model for enterprise media node requests."""
import uuid

from django.db import models


class MediaNodeInterest(models.Model):
    """A sales lead for the (private/enterprise) media node workflow.

    The media node installation stack is proprietary; the OSS frontend only
    collects contact details and hands off to the sales workflow.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        WON = "won", "Won"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    company = models.CharField(max_length=255, blank=True, default="")  # type: ignore[var-annotated]
    email = models.EmailField(max_length=255)  # type: ignore[var-annotated]
    host = models.CharField(max_length=255, blank=True, default="")  # type: ignore[var-annotated]
    notes = models.TextField(blank=True, default="")  # type: ignore[var-annotated]
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,  # type: ignore[var-annotated]
    )
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Media Node Interest"

    def __str__(self):
        return f"Interest({self.email} / {self.company or self.name})"