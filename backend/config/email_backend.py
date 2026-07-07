"""
Email backend that reads SMTP configuration from PlatformConfig at send time.

This bridges the gap between the admin UI (which writes to PlatformConfig)
and Django's email system (which normally only reads from settings/env vars).

Fallback chain:
  1. PlatformConfig.smtp_host  →  use PlatformConfig values
  2. Env vars (EMAIL_HOST etc.) →  use Django defaults
"""
import logging

from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

logger = logging.getLogger(__name__)


class PlatformConfigEmailBackend(SMTPBackend):
    """SMTP backend that loads host/port/credentials from PlatformConfig on every send.

    When PlatformConfig.smtp_host is set, all SMTP params come from the DB.
    When it is empty, falls back to django.conf.settings.EMAIL_* values.
    """

    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None,
                 ssl_keyfile=None, ssl_certfile=None, **kwargs):
        super().__init__(
            host=host, port=port, username=username, password=password,
            use_tls=use_tls, fail_silently=fail_silently, use_ssl=use_ssl,
            timeout=timeout, ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile, **kwargs,
        )
        self._pc_loaded = False

    def _ensure_config(self):
        if self._pc_loaded:
            return
        self._pc_loaded = True
        try:
            from apps.deployments.models_core import PlatformConfig
            config = PlatformConfig.load()
            if not config.smtp_host:
                return
            self.host = config.smtp_host
            self.port = config.smtp_port or 587
            if config.smtp_username:
                self.username = config.smtp_username
            if config.smtp_password:
                self.password = config.smtp_password
            self.use_tls = config.smtp_use_tls
            self.use_ssl = not config.smtp_use_tls and config.smtp_port == 465
        except Exception:
            logger.debug("Could not load SMTP config from PlatformConfig", exc_info=True)

    def send_messages(self, email_messages):
        self._ensure_config()
        return super().send_messages(email_messages)
