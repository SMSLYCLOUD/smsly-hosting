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
        return True  # Assume serving if we can't determine (e.g. embedded)

    command = argv[0]
    # Include common web servers and management commands that serve traffic
    serving_commands = {"gunicorn", "uvicorn", "daphne", "runserver", "runserver_plus"}
    return any(cmd in " ".join(argv) for cmd in serving_commands)


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

        # 3. Dynamic Domain Patching (Zero Trust Whitelisting)
        try:
            from .patching import patch_runtime_settings
            patch_runtime_settings()
        except Exception:
            pass

        # Startup proxy sync is opt-in because AppConfig.ready() must not
        # perform database/proxy side effects during management commands.
        if not getattr(settings, 'IS_TESTING', False) and _is_serving_process():
            try:
                from .startup import schedule_startup_caddy_sync
                schedule_startup_caddy_sync()
            except Exception:
                pass
