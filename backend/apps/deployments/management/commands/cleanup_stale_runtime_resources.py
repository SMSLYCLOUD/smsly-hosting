import logging
from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
    help = "Alias for find_stale_runtime_resources --apply"

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Delete stale resources.')

    def handle(self, *args, **options):
        if not options.get('apply'):
            self.stdout.write(self.style.ERROR("Must specify --apply to actually delete. Use find_stale_runtime_resources --dry-run for testing."))
            return

        call_command('find_stale_runtime_resources', apply=True)
