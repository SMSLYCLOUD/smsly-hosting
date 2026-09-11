"""
Prune orphaned project-scoped image namespaces from the platform registry.

Ecosystem (and project-scoped) builds push into ``proj-<id8>/`` namespaces
(see ``project_image_namespace``). When a project is deleted its images stay
behind — the registry never garbage-collects them on its own. This command
lists ``proj-*`` repositories whose project row no longer exists and, with
``--apply``, deletes every tag in them.

Safety:
  * DRY-RUN BY DEFAULT. Nothing is deleted without ``--apply``.
  * Only ``proj-<8 hex>`` namespaces are considered — the global ``smsly/``
    namespace (platform images, legacy builds) is never touched.
  * Only namespaces whose project row is GONE are pruned. Projects that
    exist but have no services are left alone (a deploy may be in flight).
  * Requires REGISTRY_STORAGE_DELETE_ENABLED=true on the registry
    (the platform compose sets it); otherwise deletes 405 and the
    command reports them as failed, deleting nothing.

Usage:
    python manage.py prune_orphaned_registry_namespaces [--apply]
"""

import logging
import re

import requests
import urllib3
from django.core.management.base import BaseCommand
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

try:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

_PROJ_NS_RE = re.compile(r"^proj-[0-9a-f]{8}$")
_REGISTRY_HOST = "registry:5000"


class Command(BaseCommand):
    help = "List (default) or delete (--apply) orphaned proj-* image namespaces."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete tags. Without it, only lists what would be deleted.",
        )

    def handle(self, *args, **options):
        from apps.deployments.models.registry_scope import ScopedRegistry
        from apps.deployments.models.service import Project

        apply = bool(options.get("apply"))
        try:
            info = ScopedRegistry.resolve_registry_credentials(None) or {}
        except Exception as exc:
            self.stderr.write(f"Could not resolve platform registry credentials: {exc}")
            return
        username = str(info.get("username") or "")
        password = str(info.get("password") or "")
        if not username or not password:
            self.stderr.write(
                "No platform registry credential configured "
                "(PlatformConfig registry_user/registry_password). Aborting."
            )
            return
        auth = HTTPBasicAuth(username, password)
        base = f"https://{_REGISTRY_HOST}"

        try:
            repos = self._get_json(f"{base}/v2/_catalog", auth).get("repositories", [])
        except Exception as exc:
            self.stderr.write(f"Registry catalog unreachable: {exc}")
            return

        orphan_repos = []
        # UUIDField has no portable startswith lookup — match id8 prefixes
        # in Python (project counts are small; one query total).
        live_prefixes = {
            str(pid).replace("-", "")[:8].lower()
            for pid in Project.objects.values_list("id", flat=True)
        }
        for repo in repos:
            ns = str(repo).split("/", 1)[0] if "/" in str(repo) else ""
            if not _PROJ_NS_RE.match(ns):
                continue
            if ns.split("-", 1)[1] not in live_prefixes:
                orphan_repos.append(str(repo))

        if not orphan_repos:
            self.stdout.write("No orphaned proj-* namespaces found.")
            return

        self.stdout.write(
            f"{'WOULD DELETE' if not apply else 'DELETING'} "
            f"{len(orphan_repos)} orphaned repositorie(s):"
        )
        deleted_tags = 0
        failed = []
        for repo in sorted(orphan_repos):
            try:
                tags = self._get_json(f"{base}/v2/{repo}/tags/list", auth).get("tags", [])
            except Exception as exc:
                failed.append(f"{repo} (tags list failed: {exc})")
                continue
            if not tags:
                self.stdout.write(f"  {repo}: no tags")
                continue
            for tag in tags:
                if not apply:
                    self.stdout.write(f"  {repo}:{tag}")
                    continue
                try:
                    digest = self._tag_digest(base, auth, repo, tag)
                    self._delete(f"{base}/v2/{repo}/manifests/{digest}", auth)
                    deleted_tags += 1
                    self.stdout.write(f"  deleted {repo}:{tag}")
                except Exception as exc:
                    failed.append(f"{repo}:{tag} ({exc})")
        if not apply:
            self.stdout.write("Dry run — rerun with --apply to delete.")
        else:
            self.stdout.write(f"Deleted {deleted_tags} tag(s).")
        if failed:
            self.stderr.write(f"{len(failed)} failure(s):")
            for item in failed:
                self.stderr.write(f"  {item}")

    @staticmethod
    def _get_json(url: str, auth) -> dict:
        # Internal registry with a private CA: the daemon trusts it via
        # installed certs, but this container may not — same posture as
        # the check_registry --insecure diagnostic path.
        resp = requests.get(url, auth=auth, verify=False, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _tag_digest(base: str, auth, repo: str, tag: str) -> str:
        resp = requests.get(
            f"{base}/v2/{repo}/manifests/{tag}",
            auth=auth,
            verify=False,
            timeout=15,
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
        )
        resp.raise_for_status()
        digest = resp.headers.get("Docker-Content-Digest", "")
        if not digest:
            raise RuntimeError("registry did not return Docker-Content-Digest")
        return digest

    @staticmethod
    def _delete(url: str, auth) -> None:
        resp = requests.delete(url, auth=auth, verify=False, timeout=30)
        resp.raise_for_status()
