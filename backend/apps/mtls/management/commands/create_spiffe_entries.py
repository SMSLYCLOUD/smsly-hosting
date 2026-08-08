"""
Django management command to create/update SPIRE registration entries.

Usage:
    python manage.py create_spiffe_entries
    python manage.py create_spiffe_entries --server ecosystem
    python manage.py create_spiffe_entries --server platform

This command:
1. Reads all deployed services from the database
2. Creates SPIRE registration entries for each service with mTLS enabled
3. Uses Docker label selectors for workload attestation
4. Sets DNS aliases for service discovery

Run this command after every deployment to keep SPIRE entries in sync.
"""

import json
import os
import subprocess
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

ECOSYSTEM_SPIRE_SERVER_CONTAINER = os.getenv(
    "SPIRE_ECOSYSTEM_SERVER_CONTAINER", "smsly-spire-server-ecosystem"
)
PLATFORM_SPIRE_SERVER_CONTAINER = os.getenv(
    "SPIRE_SERVER_CONTAINER", "smsly-spire-server"
)
SPIRE_SERVER_SOCKET = "/opt/spire/data/server.sock"


class Command(BaseCommand):
    help = "Create/update SPIRE registration entries for all deployed services"

    def add_arguments(self, parser):
        parser.add_argument(
            "--server",
            choices=["ecosystem", "platform", "both"],
            default="ecosystem",
            help="Which SPIRE server to create entries for (default: ecosystem)",
        )
        parser.add_argument(
            "--trust-domain",
            default=None,
            help="SPIFFE trust domain (auto-selected based on --server if not set)",
        )
        parser.add_argument(
            "--svid-ttl",
            type=int,
            default=3600,
            help="SVID TTL in seconds (default: 3600 = 1 hour)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print entries without creating them",
        )

    def handle(self, *args, **options):
        server = options["server"]
        svid_ttl = options["svid_ttl"]
        dry_run = options["dry_run"]
        trust_domain_override = options["trust_domain"]

        servers = {
            "ecosystem": {
                "container": ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                "trust_domain": trust_domain_override or os.getenv("ECOSYSTEM_TRUST_DOMAIN", "ecosystem.local"),
                "dns_suffix": "ecosystem.svc",
            },
            "platform": {
                "container": PLATFORM_SPIRE_SERVER_CONTAINER,
                "trust_domain": trust_domain_override or os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local"),
                "dns_suffix": "paas.svc",
            },
        }

        targets = [server] if server != "both" else ["ecosystem", "platform"]

        for target in targets:
            cfg = servers[target]
            self._sync_server(cfg, svid_ttl, dry_run)

    def _sync_server(self, cfg, svid_ttl, dry_run):
        trust_domain = cfg["trust_domain"]
        container = cfg["container"]
        dns_suffix = cfg["dns_suffix"]

        self.stdout.write(f"\n=== Syncing {container} (trust_domain={trust_domain}) ===")

        try:
            from apps.mtls.models import MtlsConfig
            configs = MtlsConfig.objects.filter(
                enabled=True, trust_domain=trust_domain
            ).select_related("service")
        except ImportError:
            self.stdout.write(self.style.WARNING(
                "MtlsConfig model not found. Skipping dynamic entries."
            ))
            configs = []

        entries = []
        for config in configs:
            service_name = config.service.name
            entry = {
                "spiffe_id": f"spiffe://{trust_domain}/service/{service_name}",
                "parent_id": f"spiffe://{trust_domain}/spire-server",
                "selectors": [f"docker:label:com.paas.service:{service_name}"],
                "ttl": svid_ttl,
                "dns": [service_name, f"{service_name}.{dns_suffix}"],
            }
            entries.append(entry)

        if not entries:
            self.stdout.write(self.style.WARNING("No entries to create."))
            return

        self.stdout.write(f"Creating {len(entries)} SPIRE registration entries...")

        if dry_run:
            self.stdout.write(json.dumps({"entries": entries}, indent=2))
            return

        created = 0
        errors = 0

        for entry in entries:
            try:
                cmd = [
                    "docker", "exec", container,
                    "/opt/spire/bin/spire-server", "entry", "create",
                    "-socketPath", SPIRE_SERVER_SOCKET,
                    "-spiffeID", entry["spiffe_id"],
                    "-parentID", entry["parent_id"],
                    "-ttl", str(entry["ttl"]),
                ]

                for selector in entry["selectors"]:
                    cmd.extend(["-selector", selector])

                for dns in entry["dns"]:
                    cmd.extend(["-dns", dns])

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    created += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  Created: {entry['spiffe_id']}"
                    ))
                else:
                    if "already exists" in result.stderr:
                        self.stdout.write(f"  Exists: {entry['spiffe_id']}")
                    else:
                        errors += 1
                        self.stdout.write(self.style.ERROR(
                            f"  Failed: {entry['spiffe_id']} - {result.stderr.strip()}"
                        ))

            except subprocess.TimeoutExpired:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  Timeout: {entry['spiffe_id']}"
                ))
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  Error: {entry['spiffe_id']} - {e}"
                ))

        self.stdout.write(f"Done. Created: {created}, Errors: {errors}, Total: {len(entries)}")
