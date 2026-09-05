"""Remove unused project/ecosystem Docker networks safely."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Remove empty smsly-net-* networks that are not referenced by an "
        "active project scope. Use --dry-run first."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        import docker

        from apps.deployments.models.network_scope import ScopedNetwork

        client = docker.from_env()
        protected = {
            "smsly-net",
            "smsly-platform-net",
            "smsly-proxy",
            "smsly-hosting_traefik-proxy-net",
        }
        protected.update(
            ScopedNetwork.objects.filter(isolated=True)
            .exclude(network_name="")
            .values_list("network_name", flat=True)
        )
        protected.update(
            f"smsly-net-{str(scope.object_id).replace('-', '')[:8]}"
            for scope in ScopedNetwork.objects.filter(isolated=True)
            if not scope.network_name
        )

        removed = 0
        skipped = 0
        for network in client.networks.list():
            name = network.name
            if not name.startswith("smsly-net-") or name in protected:
                continue
            # networks.list() returns summary attrs WITHOUT the Containers
            # map — a full reload is required, otherwise every network looks
            # empty and live ones (e.g. Traefik-only attachments) would be
            # removed. Never skip this reload.
            try:
                network.reload()
            except Exception as exc:
                self.stderr.write(f"could not inspect {name}: {exc}")
                skipped += 1
                continue
            containers = (network.attrs.get("Containers") or {})
            if containers:
                skipped += 1
                continue
            self.stdout.write(f"unused scoped network: {name}")
            if not options["dry_run"]:
                try:
                    network.remove()
                    removed += 1
                except Exception as exc:  # Docker can race a new deployment.
                    self.stderr.write(f"could not remove {name}: {exc}")
            else:
                removed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"scoped network cleanup: {'would remove' if options['dry_run'] else 'removed'} "
                f"{removed}; skipped active networks {skipped}"
            )
        )
