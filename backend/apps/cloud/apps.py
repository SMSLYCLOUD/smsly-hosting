"""Apps module."""
from django.apps import AppConfig


class CloudConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.cloud'

    def ready(self):
        # Globally patch docker.from_env across all threads and workers
        try:
            import apps.cloud.docker_client
        except Exception:
            pass

