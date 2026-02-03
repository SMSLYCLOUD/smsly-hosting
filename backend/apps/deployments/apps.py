"""Apps module."""
from django.apps import AppConfig


class DeploymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments'

    def ready(self):
        # Import models to ensure they are registered
        from . import models
        from . import models_addons
        from . import models_metrics
        from . import models_templates
        from . import models_cron
        from . import models_storage
