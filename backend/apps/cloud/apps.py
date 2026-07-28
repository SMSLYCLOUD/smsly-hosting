"""Apps module."""
from django.apps import AppConfig


class CloudConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.cloud'

    def ready(self):
        pass

