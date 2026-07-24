"""Models Storage module."""
import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Volume(models.Model):
    # Aligned with name validator in views_storage.py
    _VOLUME_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    # Use string reference to avoid circular import
    service = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Service',
        on_delete=models.CASCADE,
        related_name='volumes')

    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    mount_path = models.CharField(max_length=255,  # type: ignore[var-annotated]
                                  help_text="Path inside container e.g. /data")
    size_gb = models.IntegerField(  # type: ignore[var-annotated]
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
    )

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    def clean(self):
        """Defence-in-depth: a model-level validator catches direct DB
        writes that bypass the serializer. The serializer in
        views_storage.py runs first; this is the second line of defence
        against Volume rows that would let a tenant mount /var/run/
        docker.sock or other host directories into their container.
        """
        super().clean()
        # Avoid circular import
        from .views.storage import _validate_volume_mount_path, _validate_volume_name
        try:
            _validate_volume_name(self.name)
        except Exception as exc:
            raise ValidationError({"name": str(exc)})
        try:
            _validate_volume_mount_path(self.mount_path)
        except Exception as exc:
            raise ValidationError({"mount_path": str(exc)})

    def __str__(self):
        return f"{self.name} ({self.mount_path})"

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(size_gb__gte=1) & models.Q(size_gb__lte=1000),  # type: ignore[var-annotated]
                name="volume_size_gb_range",
            ),
        ]
