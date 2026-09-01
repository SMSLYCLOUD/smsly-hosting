"""Edge Shield deployment command.

Usage:
    python manage.py deploy_edge_shield                # apply everything
    python manage.py deploy_edge_shield --dry-run     # verify only
    python manage.py deploy_edge_shield --no-lockdown # skip iptables
    python manage.py deploy_edge_shield --no-dnssec   # skip DNSSEC
    python manage.py deploy_edge_shield --rollback    # proxy off? NO —
                                                      # rollback only
                                                      # undoes lockdown

The command is idempotent: re-running re-fetches Cloudflare ranges and
re-asserts zone state.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.deployments.models import PlatformConfig
from apps.deployments.services.edge_shield import (
    deploy_edge_shield,
    verify_edge_shield,
)


class Command(BaseCommand):
    help = (
        "Edge Shield: protect the platform against BGP/route hijacks — "
        "Cloudflare-proxied records (Anycast), TLS full, HSTS, origin "
        "lockdown (80/443 CF-only), DNSSEC + DS record."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Verify current shield state without changing anything.",
        )
        parser.add_argument(
            "--no-lockdown", action="store_true",
            help="Skip the host iptables lockdown (e.g. testing proxy first).",
        )
        parser.add_argument(
            "--no-dnssec", action="store_true",
            help="Skip enabling DNSSEC on the zone.",
        )
        parser.add_argument(
            "--no-proxy", action="store_true",
            help="Skip proxying records (lockdown + DNSSEC only).",
        )
        parser.add_argument(
            "--rollback-lockdown", action="store_true",
            help="Remove the origin iptables lockdown (emergency access).",
        )

    def handle(self, *args, **opts):
        config = PlatformConfig.load()
        domain = (config.domain or "").strip()
        token = (config.cloudflare_api_token or "").strip()

        if not domain or not token:
            raise CommandError(
                "PlatformConfig.domain and cloudflare_api_token must be set "
                "(Settings → Domains). The token needs Zone DNS + Zone "
                "Settings + Zone DNSSEC edit scopes."
            )

        if opts["dry_run"]:
            report = verify_edge_shield(config)
        else:
            report = deploy_edge_shield(
                config,
                enable_proxy=not opts["no_proxy"],
                enable_dnssec=not opts["no_dnssec"],
                enable_lockdown=not opts["no_lockdown"],
            )

        for step in report.steps:
            marker = {
                "ok": self.style.SUCCESS("✓"),
                "warn": self.style.WARNING("⚠"),
                "error": self.style.ERROR("✗"),
            }.get(step["status"], " ")
            self.stdout.write(f"  {marker} {step['step']}: {step['detail']}")

        if report.ok:
            self.stdout.write(self.style.SUCCESS("Edge Shield applied/verified."))
        else:
            for err in report.errors:
                self.stdout.write(self.style.ERROR(f"  error: {err}"))
            raise CommandError("Edge Shield incomplete — see errors above.")

        if getattr(config, "edge_shield_ds_record", ""):
            self.stdout.write("")
            self.stdout.write(self.style.MGMT(
                "NEXT STEP (registrar): add this DS record for the zone at "
                "your registrar to complete DNSSEC:"
            ))
            self.stdout.write(f"  {config.edge_shield_ds_record}")
