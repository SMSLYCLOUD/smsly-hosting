"""
Project model — groups related services together (Railway-style).
"""

import uuid
import re

from django.conf import settings
from django.db import models


class Project(models.Model):
    """
    Represents a logical grouping of services, similar to Railway projects
    or Vercel project scopes. Services belong to a project; projects belong 
    to a user (team support can wrap this later).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable project name, e.g. 'SMSLY Platform'",
    )
    slug = models.SlugField(
        max_length=120,
        help_text="URL-safe identifier, auto-generated from name",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional description of what this project contains",
    )
    icon_emoji = models.CharField(
        max_length=10,
        default="📦",
        help_text="Emoji icon shown in the UI",
    )
    color = models.CharField(
        max_length=7,
        default="#6366f1",
        help_text="Hex color for project accent, e.g. '#10b981'",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="If true, new services without a project are assigned here",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-updated_at"]
        unique_together = ("owner", "slug")
        verbose_name = "Project"

    def __str__(self):
        return f"{self.icon_emoji} {self.name}"

    def save(self, *args, **kwargs):
        # Auto-generate slug from name if not set
        if not self.slug:
            base_slug = re.sub(r'[^a-z0-9]+', '-', self.name.lower()).strip('-')[:100]
            self.slug = base_slug or "project"
            # Ensure uniqueness within owner scope
            counter = 1
            original_slug = self.slug
            while (
                Project.objects.filter(owner=self.owner, slug=self.slug)
                .exclude(pk=self.pk)
                .exists()
            ):
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)
