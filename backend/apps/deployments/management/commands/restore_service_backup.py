"""Restore a service backup archive on this server.

Used by transfer engine after uploading a backup tarball to a target server.
"""

from __future__ import annotations

import json
import os
import tarfile

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.deployments.models import Service
from apps.deployments.models.backup import ServiceBackup
from apps.deployments.services.backup_service import BackupService


def _extract_service_name_from_backup(file_path: str) -> str:
    try:
        with tarfile.open(file_path, "r:gz") as tar:
            member = tar.getmember("metadata.json")
            fp = tar.extractfile(member)
            if fp is None:
                return ""
            try:
                metadata = json.loads(fp.read().decode("utf-8"))
            finally:
                fp.close()
            return str(metadata.get("service_name") or "").strip()
    except Exception:
        return ""


def _resolve_owner(owner_id: str | None):
    User = get_user_model()
    if owner_id:
        owner = User.objects.filter(id=owner_id).first()
        if owner:
            return owner

    # Prefer superuser, then any user.
    owner = User.objects.filter(is_superuser=True).order_by("id").first()
    if owner:
        return owner
    return User.objects.order_by("id").first()


class Command(BaseCommand):
    help = "Restore a service backup tarball to an existing or new service."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to backup .tar.gz")
        parser.add_argument(
            "--service-id",
            required=False,
            help="Optional target service UUID. If not found, falls back to service-name/new service.",
        )
        parser.add_argument(
            "--service-name",
            required=False,
            help="Optional target service name fallback.",
        )
        parser.add_argument(
            "--owner-id",
            required=False,
            help="Owner user ID for creating a new service when needed.",
        )

    def handle(self, *args, **options):
        file_path = str(options["file"]).strip()
        service_id = str(options.get("service_id") or "").strip()
        service_name = str(options.get("service_name") or "").strip()
        owner_id = str(options.get("owner_id") or "").strip()

        if not file_path or not os.path.exists(file_path):
            raise CommandError(f"Backup file not found: {file_path}")

        backup_service = BackupService()
        target_service = None

        if service_id:
            target_service = Service.objects.filter(id=service_id).first()

        if target_service is None and service_name:
            target_service = Service.objects.filter(name=service_name).first()

        if target_service is None:
            inferred_name = _extract_service_name_from_backup(file_path)
            if inferred_name:
                target_service = Service.objects.filter(name=inferred_name).first()

        if target_service is not None:
            temp_backup = ServiceBackup.objects.create(
                service=target_service,
                created_by=target_service.owner,
                status="COMPLETED",
                backup_type="PRE_TRANSFER",
                file_path=file_path,
            )
            try:
                backup_service.restore_service(
                    str(temp_backup.id),
                    target_service_id=str(target_service.id),
                )
            finally:
                temp_backup.delete()
            self.stdout.write(self.style.SUCCESS(f"Restored backup into service {target_service.name}"))
            return

        owner = _resolve_owner(owner_id or None)
        if owner is None:
            raise CommandError(
                "No users exist on target server to own a newly restored service. "
                "Create a user first or pass --service-id for an existing service."
            )

        backup_service._restore_service_from_file(file_path, owner=owner)  # pylint: disable=protected-access
        self.stdout.write(self.style.SUCCESS(f"Restored backup into a service owned by {owner.username}"))
