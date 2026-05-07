"""Apps module."""
import os
import sys
from pathlib import Path

from django.apps import AppConfig


def _is_serving_process() -> bool:
    """Return true only for processes expected to serve web traffic."""
    if os.environ.get("SMSLY_ENABLE_STARTUP_CADDY_SYNC", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    if os.environ.get("SMSLY_DISABLE_STARTUP_TASKS", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    argv = [Path(str(arg)).name for arg in sys.argv if arg]
    if not argv:
        return False

    command = argv[0]
    if command in {"gunicorn", "uvicorn", "daphne"}:
        return True

    return command in {"manage.py", "django-admin"} and any(
        arg in {"runserver", "runserver_plus"} for arg in argv[1:]
    )


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

        # Startup proxy sync is opt-in because AppConfig.ready() must not
        # perform database/proxy side effects during management commands.
        from django.conf import settings
        if not getattr(settings, 'IS_TESTING', False) and _is_serving_process():
            try:
                from .startup import schedule_startup_caddy_sync
                schedule_startup_caddy_sync()
            except Exception:
                pass
