from django.apps import AppConfig


class MtlsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.mtls'
    verbose_name = 'mTLS'

    def ready(self):
        from apps.mtls import signals  # noqa: F401
