"""Apps module."""
import contextlib
import os
import sys
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.db.backends.signals import connection_created


def _on_first_db_connection(sender, connection, **kwargs):
    """Run DB-dependent startup work on the first connection of each worker.

    These queries are intentionally deferred out of ``AppConfig.ready()`` so
    they do not trigger Django's "Accessing the database during app
    initialization" ``RuntimeWarning`` (which fires 4× per restart, once per
    gunicorn worker). The ``connection_created`` signal fires the first time
    a worker opens a DB connection -- i.e. during the first request -- so
    ALLOWED_HOSTS, the Prometheus gauge, and the docker-labels target files
    are all populated before the second request arrives, with no warning in
    the logs. The handler disconnects itself so it runs exactly once per
    worker process.
    """
    try:
        from .patching import patch_runtime_settings
        patch_runtime_settings()
    except Exception:
        pass

    try:
        from config.metrics import SERVICES_ACTIVE

        from .models import Service
        SERVICES_ACTIVE.set(
            Service.objects.filter(status=Service.Status.ACTIVE).count()
        )
    except Exception:
        pass

    connection_created.disconnect(_on_first_db_connection)


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

    # Include common web servers and management commands that serve traffic
    serving_commands = {"gunicorn", "uvicorn", "daphne", "runserver", "runserver_plus"}
    return any(cmd in " ".join(argv) for cmd in serving_commands)


class DeploymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.deployments'

    def ready(self):
        # Patch EncryptedMixin to prevent ciphertext leaks on decryption failure
        try:
            import logging
            import time

            from encrypted_model_fields.fields import EncryptedMixin

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

        # Import signals
        with contextlib.suppress(ImportError):
            from . import signals  # noqa: F401

        # Defer DB-dependent startup work (Prometheus gauge + runtime settings
        # patch) to the first DB connection of each worker. Running these in
        # ready() would trigger Django's "Accessing the database during app
        # initialization" RuntimeWarning. See _on_first_db_connection.
        connection_created.connect(_on_first_db_connection)

        # Startup proxy sync is opt-in because AppConfig.ready() must not
        # perform database/proxy side effects during management commands.
        if not getattr(settings, 'IS_TESTING', False) and _is_serving_process():
            try:
                from .startup import schedule_startup_caddy_sync
                schedule_startup_caddy_sync()
            except Exception:
                pass
