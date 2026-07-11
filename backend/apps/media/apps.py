"""Apps module for Media Node management."""
from django.apps import AppConfig


class MediaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.media'
    verbose_name = 'Media Nodes'

    def ready(self):
        pass
