"""
Django management command to create/update SPIRE registration entries.

Usage:
    python manage.py create_spiffe_entries

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
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create/update SPIRE registration entries for all deployed services"

    def add_arguments(self, parser):
        parser.add_argument(
            "--trust-domain",
            default=os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local"),
            help="SPIFFE trust domain (default: platform.local)",
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
        parser.add_argument(
            "--spire-server-socket",
            default="/opt/spire/data/server.sock",
            help="SPIRE server socket path",
        )

    def handle(self, *args, **options):
        trust_domain = options["trust_domain"]
        svid_ttl = options["svid_ttl"]
        dry_run = options["dry_run"]
        server_socket = options["spire_server_socket"]

        # Get all services with mTLS enabled
        try:
            from apps.mtls.models import MtlsConfig
            configs = MtlsConfig.objects.filter(enabled=True).select_related("service")
        except ImportError:
            self.stdout.write(self.style.WARNING(
                "MtlsConfig model not found. Using static entries from registration_entries.json"
            ))
            configs = None

        entries = []

        if configs:
            # Dynamic entries from database
            for config in configs:
                service_name = config.service.name
                entry = {
                    "spiffe_id": {
                        "trust_domain": trust_domain,
                        "path": f"/service/{service_name}",
                    },
                    "parent_id": {
                        "trust_domain": trust_domain,
                        "path": "/spire-server",
                    },
                    "selectors": [
                        {"type": "docker", "value": f"label:com.paas.service:{service_name}"},
                    ],
                    "x509_svid_ttl": svid_ttl,
                    "dns_names": [
                        service_name,
                        f"{service_name}.paas.svc",
                    ],
                }
                entries.append(entry)
        else:
            # Static entries from file
            entries_file = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "..", "..",
                "infrastructure", "spire", "registration_entries.json"
            )
            if os.path.exists(entries_file):
                with open(entries_file) as f:
                    data = json.load(f)
                    entries = data.get("entries", [])
                    # Replace trust domain placeholder
                    entries = json.loads(
                        json.dumps(entries).replace("TRUST_DOMAIN_PLACEHOLDER", trust_domain)
                    )

        if not entries:
            self.stdout.write(self.style.WARNING("No entries to create."))
            return

        self.stdout.write(f"Creating {len(entries)} SPIRE registration entries...")

        if dry_run:
            self.stdout.write(json.dumps({"entries": entries}, indent=2))
            return

        # Create entries via SPIRE server CLI
        created = 0
        errors = 0

        for entry in entries:
            try:
                # Build spire-server entry create command
                cmd = [
                    "docker", "exec", "smsly-spire-server",
                    "/opt/spire/bin/spire-server", "entry", "create",
                    "-socketPath", server_socket,
                    "-spiffeID", f"spiffe://{entry['spiffe_id']['trust_domain']}{entry['spiffe_id']['path']}",
                    "-parentID", f"spiffe://{entry['parent_id']['trust_domain']}{entry['parent_id']['path']}",
                    "-ttl", str(entry.get("x509_svid_ttl", svid_ttl)),
                ]

                # Add selectors
                for selector in entry.get("selectors", []):
                    cmd.extend(["-selector", f"{selector['type']}:{selector['value']}"])

                # Add DNS names
                for dns in entry.get("dns_names", []):
                    cmd.extend(["-dns", dns])

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    created += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  Created: {entry['spiffe_id']['path']}"
                    ))
                else:
                    # Entry might already exist
                    if "already exists" in result.stderr:
                        self.stdout.write(f"  Exists: {entry['spiffe_id']['path']}")
                    else:
                        errors += 1
                        self.stdout.write(self.style.ERROR(
                            f"  Failed: {entry['spiffe_id']['path']} - {result.stderr.strip()}"
                        ))

            except subprocess.TimeoutExpired:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  Timeout: {entry['spiffe_id']['path']}"
                ))
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  Error: {entry['spiffe_id']['path']} - {e}"
                ))

        self.stdout.write("")
        self.stdout.write(f"Done. Created: {created}, Errors: {errors}, Total: {len(entries)}")
