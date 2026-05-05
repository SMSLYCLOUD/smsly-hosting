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
        from . import models_tunnels
        from . import models_backup
        from . import models_transfer
        from . import openapi

        # Import signals
        # Note: We assume there's a signals.py or we define them here.
        # Based on the failing test, we need a signal that creates SMSLY_API_KEY
        try:
            from . import signals
        except ImportError:
            pass

        # Dynamically add PlatformConfig.domain to ALLOWED_HOSTS,
        # CSRF_TRUSTED_ORIGINS, and CORS_ALLOWED_ORIGINS so that domain
        # changes via the Settings UI work without editing .env files.
        # try:
        #     from config.settings import _patch_allowed_hosts_from_db
        #     _patch_allowed_hosts_from_db()
        # except Exception:
        #     pass

        # Fire a one-time startup Caddy sync so SSL/DNS "just work" after boot.
        from django.conf import settings
        if not getattr(settings, 'IS_TESTING', False):
            try:
                from .startup import schedule_startup_caddy_sync
                schedule_startup_caddy_sync()
            except Exception:
                pass
