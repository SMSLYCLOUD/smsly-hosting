from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Service, EnvironmentVariable
import secrets

@receiver(post_save, sender=Service)
def create_default_env_vars(sender, instance, created, **kwargs):
    if created:
        # Inject SMSLY_API_KEY
        # In a real app, this would be a JWT or a specific service token
        api_key = f"smsly_{secrets.token_urlsafe(32)}"
        EnvironmentVariable.objects.create(
            service=instance,
            key='SMSLY_API_KEY',
            value=api_key,
            is_secret=True
        )
