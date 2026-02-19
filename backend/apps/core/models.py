from django.db import models
from django.conf import settings

class APIKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=128)  # bcrypt hash, never store raw
    prefix = models.CharField(max_length=8)  # first 8 chars shown to user
    last_used = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"
