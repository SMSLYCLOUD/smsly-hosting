"""
Manage SOPS-age secret bundles (default secrets backend).

Usage:
    python manage.py sync_secrets_sops --ensure-key
    python manage.py sync_secrets_sops --export --service <uuid>
    python manage.py sync_secrets_sops --export-all
    python manage.py sync_secrets_sops --verify --service <uuid>
    python manage.py sync_secrets_sops --verify-all

Bundles live in /app/backups/secrets/*.enc.yaml (host backups volume,
swept up by server backups). Reports key names only — values never
touch stdout/stderr.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Export/verify SOPS-age secret bundles."

    def add_arguments(self, parser):
        parser.add_argument("--ensure-key", action="store_true", help="Create the age keypair if missing")
        parser.add_argument("--export", action="store_true", help="Export one service's bundle")
        parser.add_argument("--export-all", action="store_true", help="Export bundles for all services with secrets")
        parser.add_argument("--verify", action="store_true", help="Verify one service's bundle against DB rows")
        parser.add_argument("--verify-all", action="store_true", help="Verify all existing bundles")
        parser.add_argument("--service", type=str, default="", help="Service UUID for --export/--verify")

    def handle(self, *args, **options):
        from apps.deployments.models import Service
        from apps.deployments.services import secrets_sops

        if options.get("ensure_key"):
            public, _ = secrets_sops.ensure_age_keypair()
            self.stdout.write(f"Age recipient: {public[:18]}...")
            return

        if options.get("export_all") or options.get("verify_all"):
            mode = "export" if options.get("export_all") else "verify"
            ok, failed = 0, []
            for svc in Service.objects.all().only("id", "name"):
                try:
                    if mode == "export":
                        res = secrets_sops.export_service_bundle(svc)
                    else:
                        res = secrets_sops.verify_service_bundle(svc)
                    if res.get("ok"):
                        ok += 1
                    else:
                        failed.append(svc.name)
                except Exception as exc:
                    failed.append(f"{svc.name} ({exc})")
            self.stdout.write(f"{mode}: {ok} ok, {len(failed)} failed")
            for name in failed[:20]:
                self.stdout.write(f"  - {name}")
            return

        service_id = (options.get("service") or "").strip()
        if not service_id:
            self.stderr.write("ERROR: --service <uuid> required (or use --export-all/--verify-all)")
            return
        try:
            svc = Service.objects.get(id=service_id)
        except Exception:
            self.stderr.write("ERROR: service not found")
            return
        if options.get("verify"):
            res = secrets_sops.verify_service_bundle(svc)
            if res.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"Verified {res['secrets']} secrets for {svc.name}"))
            else:
                self.stderr.write(
                    f"MISMATCH for {svc.name}: missing={res.get('missing')} "
                    f"extra={res.get('extra')} mismatched={res.get('mismatched')}"
                )
        else:
            res = secrets_sops.export_service_bundle(svc)
            self.stdout.write(self.style.SUCCESS(
                f"Exported {res['secrets']} secrets for {svc.name} "
                f"({res['path']}, fp={res['fingerprint']})"
            ))
