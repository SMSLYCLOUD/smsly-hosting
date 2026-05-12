"""
Management command for safe node-to-node operations.

Replaces inline Python injection via docker exec with a proper
validated management command. All arguments are validated before execution.

Usage (on remote node):
  python manage.py run_node_operation <operation> --json '<payload>'

Supported operations:
  - extract_gateway_secret       Return the gateway secret from .env
  - execute_shell_code           Run trusted shell code (restricted)
  - transfer_complete            Mark a transfer as complete
"""

import json
import logging
import os
import shlex
import subprocess

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

ALLOWED_OPERATIONS = {
    "extract_gateway_secret",
    "transfer_complete",
    "get_container_logs",
}

SHELL_CODE_ALLOWLIST_PREFIXES = (
    "docker ps",
    "docker inspect",
    "docker logs",
    "cat ",
    "ls ",
    "df ",
    "free ",
)


class Command(BaseCommand):
    help = "Execute validated node-to-node operations safely"

    def add_arguments(self, parser):
        parser.add_argument("operation", type=str, help="Operation name")
        parser.add_argument("--json", type=str, default="{}", help="JSON payload")

    def handle(self, *args, **options):
        operation = options["operation"]
        try:
            payload = json.loads(options["json"])
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid JSON payload: {e}") from e

        if operation not in ALLOWED_OPERATIONS:
            raise CommandError(f"Unknown operation: {operation}. Allowed: {', '.join(sorted(ALLOWED_OPERATIONS))}")

        handler = getattr(self, f"_handle_{operation.replace('-', '_')}", None)
        if not handler:
            raise CommandError(f"No handler for operation: {operation}")

        try:
            result = handler(payload)
            self.stdout.write(json.dumps(result))
        except Exception as e:
            logger.error("Node operation %s failed: %s", operation, e)
            raise CommandError(str(e)) from e

    def _handle_extract_gateway_secret(self, payload: dict) -> dict:
        """Return the GATEWAY_SECRET from .env (santizied read-only)."""
        env_path = os.environ.get("ENV_FILE", "/app/.env")
        if not os.path.isfile(env_path):
            return {"secret": ""}
        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GATEWAY_SECRET="):
                        secret = line.split("=", 1)[1].strip().strip("\"'")
                        return {"secret": secret}
        except OSError as e:
            raise CommandError(f"Cannot read .env: {e}") from e
        return {"secret": ""}

    def _handle_transfer_complete(self, payload: dict) -> dict:
        """Execute transfer completion logic (placeholder import)."""
        transfer_id = payload.get("transfer_id")
        status = payload.get("status", "completed")
        if not transfer_id:
            raise CommandError("transfer_id is required")
        from apps.deployments.models import ServerTransfer
        try:
            transfer = ServerTransfer.objects.get(id=transfer_id)
        except ServerTransfer.DoesNotExist as e:
            raise CommandError(f"Transfer {transfer_id} not found") from e
        if status == "completed":
            transfer.status = "COMPLETED"
        elif status == "failed":
            transfer.status = "FAILED"
        else:
            raise CommandError(f"Invalid status: {status}")
        transfer.save(update_fields=["status"])
        return {"ok": True, "transfer_id": transfer_id, "status": status}

    def _handle_get_container_logs(self, payload: dict) -> dict:
        """Get container logs via Docker CLI (restricted)."""
        container_name = payload.get("container")
        tail = int(payload.get("tail", 50))
        if not container_name:
            raise CommandError("container name is required")
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True, text=True, timeout=30,
            )
            return {
                "stdout": result.stdout[-5000:],
                "stderr": result.stderr[-5000:],
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired as e:
            raise CommandError("Docker logs timed out") from e
        except FileNotFoundError as e:
            raise CommandError("Docker not found") from e
