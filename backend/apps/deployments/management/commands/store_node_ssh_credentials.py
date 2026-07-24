"""Management command to store encrypted SSH credentials for a managed server.

The credentials are encrypted at rest via EncryptedCharField/EncryptedTextField
and automatically decrypted when read back by the SSH client or self-healing system.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from apps.deployments.models.core import ManagedServer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Store SSH credentials for a managed server (encrypted at rest)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            help="IP or hostname of the managed server",
        )
        parser.add_argument(
            "--id",
            dest="server_id",
            help="UUID of the managed server",
        )
        parser.add_argument(
            "--ssh-user",
            default="root",
            help="SSH username (default: root)",
        )
        parser.add_argument(
            "--ssh-port",
            type=int,
            default=22,
            help="SSH port (default: 22)",
        )
        parser.add_argument(
            "--ssh-password",
            help="SSH password (encrypted at rest)",
        )
        parser.add_argument(
            "--ssh-key",
            help="SSH private key content (encrypted at rest)",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all managed servers that are missing SSH credentials",
        )

    def handle(self, *args, **options):
        if options.get("list"):
            self._list_missing()
            return

        server = self._resolve_server(options)

        changed = False
        if options.get("ssh_user"):
            server.ssh_user = options["ssh_user"]
            changed = True
        if options.get("ssh_port"):
            server.ssh_port = options["ssh_port"]
            changed = True
        if options.get("ssh_password"):
            server.ssh_password = options["ssh_password"]
            changed = True
        if options.get("ssh_key"):
            key_value = options["ssh_key"].strip()
            if not key_value.startswith("-----BEGIN "):
                raise CommandError(
                    "Invalid SSH private key format. Must start with '-----BEGIN ... PRIVATE KEY-----'."
                )
            if "-----END " not in key_value:
                raise CommandError(
                    "Invalid SSH private key format. Missing '-----END ... PRIVATE KEY-----' footer."
                )
            server.ssh_key = key_value
            changed = True

        if not changed:
            raise CommandError(
                "Nothing to update. Provide at least one of: "
                "--ssh-user, --ssh-port, --ssh-password, --ssh-key"
            )

        server.save(update_fields=["ssh_user", "ssh_port", "ssh_password", "ssh_key", "updated_at"])
        has_pw = bool(server.ssh_password)
        has_key = bool(server.ssh_key)

        self.stdout.write(self.style.SUCCESS(
            f"SSH credentials saved for {server.name} ({server.host})\n"
            f"  SSH user: {server.ssh_user}\n"
            f"  SSH port: {server.ssh_port}\n"
            f"  Has SSH password: {has_pw}\n"
            f"  Has SSH key: {has_key}\n"
            f"  Credentials encrypted at rest: YES"
        ))

    def _resolve_server(self, options):
        server_id = options.get("server_id")
        host = options.get("host")
        if server_id:
            try:
                return ManagedServer.objects.get(id=server_id)
            except ManagedServer.DoesNotExist:
                raise CommandError(f"Server with id={server_id} not found")
        if host:
            server = ManagedServer.objects.filter(host=host).first()
            if not server:
                raise CommandError(f"Server with host={host} not found")
            return server
        raise CommandError("Provide either --host or --id to identify the server")

    def _list_missing(self):
        servers = ManagedServer.objects.all()
        missing = [s for s in servers if not s.ssh_key and not s.ssh_password]
        if not missing:
            self.stdout.write(self.style.SUCCESS(
                "All managed servers have SSH credentials configured."
            ))
            return
        self.stdout.write(
            f"Found {len(missing)} server(s) missing SSH credentials:\n"
        )
        for s in missing:
            self.stdout.write(
                f"  {s.id} | {s.name} ({s.host}) | user={s.ssh_user} | port={s.ssh_port}"
            )
