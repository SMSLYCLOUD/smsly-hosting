"""Models Templates module."""
import uuid

from django.db import models


class Template(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    name = models.CharField(max_length=100)  # type: ignore[var-annotated]
    slug = models.SlugField(unique=True)  # type: ignore[var-annotated]
    description = models.TextField()  # type: ignore[var-annotated]
    icon_url = models.URLField(blank=True)  # type: ignore[var-annotated]
    repository_url = models.URLField()  # type: ignore[var-annotated]
    default_branch = models.CharField(max_length=50, default='main')  # type: ignore[var-annotated]
    default_port = models.IntegerField(default=8000)  # type: ignore[var-annotated]

    def __str__(self):
        return self.name
