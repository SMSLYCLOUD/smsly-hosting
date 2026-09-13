from django.db import IntegrityError, models


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
    def _get_or_create_row(cls) -> "AutoscalerConfig":
        """Fetch the singleton row, tolerating concurrent inserts.

        Two beat workers starting on an empty table can both pass the
        get_or_create lookup and race the INSERT — the loser gets
        IntegrityError instead of a row. Re-read in that case.
        """
        try:
            obj, _ = cls.objects.get_or_create(pk=1, defaults={"data": {}})
            return obj
        except IntegrityError:
            return cls.objects.get(pk=1)

    @classmethod
    def get_config(cls) -> dict:
        """Return the current config dict (creates a default row if missing)."""
        return cls._get_or_create_row().data

    @classmethod
    def save_config(cls, new_data: dict) -> dict:
        """Update the stored config with new data (replaces the dict)."""
        obj = cls._get_or_create_row()
        obj.data = new_data
        obj.save()
        return obj.data

    def __str__(self):
        return f"Autoscaler Config (Updated: {self.updated_at})"
