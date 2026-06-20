from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.deployments.models import ManagedServer, Service


class Command(BaseCommand):
    help = 'Repair services_count on ManagedServers'

    def handle(self, *args, **options):
        self.stdout.write("Calculating services per server...")

        # 1. Reset all counts
        ManagedServer.objects.all().update(services_count=0)

        # 2. Recalculate from Service table
        counts = Service.objects.filter(server__isnull=False).values('server').annotate(total=Count('id'))

        for entry in counts:
            server_id = entry['server']
            total = entry['total']
            ManagedServer.objects.filter(id=server_id).update(services_count=total)
            self.stdout.write(f"Updated Server {server_id}: {total} services")

        self.stdout.write(self.style.SUCCESS("Successfully repaired server service counts."))
