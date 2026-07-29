"""Management command to setup social apps."""
import os

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Setup GitHub and Google SocialApps for authentication."""
    help = 'Setup GitHub and Google SocialApps for authentication'

    def handle(self, *args, **options):
        # ensure Site domain is correct
        site = Site.objects.get(id=settings.SITE_ID)
        site.domain = settings.DOMAIN
        site.name = 'Grid'  # Updated brand name
        site.save()
        self.stdout.write(self.style.SUCCESS(f'Updated Site: {site.domain} ({site.name})'))

        providers = {
            'github': {
                'name': 'GitHub',
                'client_id': os.environ.get('GITHUB_CLIENT_ID'),
                'secret': os.environ.get('GITHUB_CLIENT_SECRET'),
            },
            'google': {
                'name': 'Google',
                'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
                'secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
            }
        }

        for provider_id, config in providers.items():
            if not config['client_id'] or not config['secret']:
                self.stdout.write(
                    self.style.WARNING(
                        f'Skipping {config["name"]}: Missing CLIENT_ID or CLIENT_SECRET env vars'
                    )
                )
                continue

            app, created = SocialApp.objects.update_or_create(
                provider=provider_id,
                defaults={
                    'name': config['name'],
                    'client_id': config['client_id'],
                    'secret': config['secret'],
                }
            )
            app.sites.add(site)
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f'{verb} {config["name"]} SocialApp'))
