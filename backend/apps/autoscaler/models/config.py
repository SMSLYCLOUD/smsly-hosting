from django.db import models


class AutoscalerConfig(models.Model):
    """
    Persistent store for autoscaler configuration.
    Only a single row (pk=1) is expected.
    """
    data = models.JSONField(default=dict)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Autoscaler Config"
        verbose_name_plural = "Autoscaler Config"

    @classmethod
    def get_config(cls) -> dict:
        """Return the current config dict (creates a default row if missing)."""
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"data": {}})
        return obj.data

    @classmethod
    def save_config(cls, new_data: dict) -> dict:
        """Update the stored config with new data."""
        obj, _ = cls.objects.get_or_create(pk=1)
        obj.data = new_data
        obj.save()
        return obj.data

    def __str__(self):
        return f"Autoscaler Config (Updated: {self.updated_at})"
