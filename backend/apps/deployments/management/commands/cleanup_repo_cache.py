"""Management command to clean up old cached repos."""
from django.core.management.base import BaseCommand
from services.repo_cache import cleanup_old_caches


class Command(BaseCommand):
    help = 'Clean up cached git repos not used in 7 days'

    def handle(self, *args, **options):
        cleanup_old_caches()
        self.stdout.write(self.style.SUCCESS('Repo cache cleanup complete'))
