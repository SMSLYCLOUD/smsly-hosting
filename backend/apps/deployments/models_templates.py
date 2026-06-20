"""Models Templates module."""
import uuid

from django.db import models


class Template(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    icon_url = models.URLField(blank=True)
    repository_url = models.URLField()
    default_branch = models.CharField(max_length=50, default='main')
    default_port = models.IntegerField(default=8000)

    def __str__(self):
        return self.name
