from django.apps import AppConfig


class CrowdsecConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.crowdsec"
    verbose_name = "CrowdSec WAF"