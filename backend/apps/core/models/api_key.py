from django.conf import settings
from django.db import models


class APIKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    name = models.CharField(max_length=100)  # type: ignore[var-annotated]
    key_hash = models.CharField(max_length=128)  # type: ignore[var-annotated]  # bcrypt hash, never store raw
    prefix = models.CharField(max_length=8)  # type: ignore[var-annotated]  # first 8 chars shown to user
    last_used = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    expires_at = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"
