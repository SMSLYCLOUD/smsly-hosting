"""Apps module."""
import os
import sys
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings


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
        # Patch EncryptedMixin to prevent ciphertext leaks on decryption failure
        try:
            from encrypted_model_fields.fields import EncryptedMixin
            import logging
            import time

            logger = logging.getLogger('encrypted_model_fields')
            original_to_python = EncryptedMixin.to_python

            # Rate-limit/dedup: track (model, field) -> next-allowed timestamp
            _decrypt_fail_cooldowns: dict[tuple, float] = {}
            _DECRYPT_FAIL_LOG_INTERVAL = 300  # log each (model, field) combo at most every 5 min

            def safe_to_python(self, value):
                res = original_to_python(self, value)
                if isinstance(res, str) and res.startswith('gAAAAA'):
                    model_name = self.model.__name__ if hasattr(self, 'model') else 'Unknown'
                    field_name = getattr(self, 'name', 'unknown')
                    key = (model_name, field_name)
                    now = time.monotonic()
                    if key not in _decrypt_fail_cooldowns or now >= _decrypt_fail_cooldowns[key]:
                        _decrypt_fail_cooldowns[key] = now + _DECRYPT_FAIL_LOG_INTERVAL
                        logger.error(
                            "DECRYPTION_FAILURE: Failed to decrypt field '%s' on model '%s'. "
                            "Returning empty string to prevent downstream crashes. "
                            "(subsequent failures for this field suppressed for %ds)",
                            field_name, model_name, _DECRYPT_FAIL_LOG_INTERVAL
                        )
                    return ""
                return res

            EncryptedMixin.to_python = safe_to_python
        except Exception as e:
            import logging
            logging.getLogger('apps.deployments').error("Failed to patch EncryptedMixin: %s", e)

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

        # Initialize prometheus gauge for active services (resets on restart)
        try:
            from config.metrics import SERVICES_ACTIVE
            from .models import Service
            SERVICES_ACTIVE.set(Service.objects.filter(status=Service.Status.ACTIVE).count())
        except Exception:
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
