import logging

logger = logging.getLogger(__name__)
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from urllib.parse import unquote, urlparse

from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.models import (
    Deployment,
)
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    is_deployment_local,
    log_exhaustive_deployment_diagnostics,
)

from .tasks_deploy_local import _docker_safe_segment


def _build_function(deployment, service) -> str:
    """Build serverless function image."""
    build_dir = None
    try:
        deployment.status = 'BUILDING'
        deployment.save()
        broadcast_status(deployment)

        if (service.health_check_path or '').strip() in {'', '/health'}:
            service.health_check_path = '/health'
            service.save(update_fields=['health_check_path', 'updated_at'])

        build_dir = tempfile.mkdtemp(prefix=f"func_{deployment.id}_")
        FunctionProvisioner.prepare_context(service, build_dir)

        safe_service_name = _docker_safe_segment(service.name, fallback="function")
        deploy_tag = str(deployment.id).replace("-", "")[:8]
        tag = f"smsly/func-{safe_service_name}:{deploy_tag}"

        append_log(deployment, f"Building function {tag}...\n")

        cmd = ["docker", "build", "-t", tag, "--load", build_dir]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            build_output = "\n".join(
                part for part in [result.stdout, result.stderr] if part
            ).strip()
            if build_output:
                append_log(deployment, f"{build_output[-4000:]}\n")
        except subprocess.TimeoutExpired as exc:
            append_log(deployment, "\n[FUNCTION-BUILD] Docker build timed out after 300s.\n")
            partial = "\n".join(
                str(part) for part in [exc.stdout, exc.stderr] if part
            ).strip()
            if partial:
                append_log(deployment, f"{partial[-4000:]}\n")
            raise
        except subprocess.CalledProcessError as exc:
            append_log(deployment, "\n[FUNCTION-BUILD] Docker build failed.\n")
            output = "\n".join(
                part for part in [exc.stdout, exc.stderr] if part
            ).strip()
            if output:
                append_log(deployment, f"{output[-8000:]}\n")
            raise

        # ── Resolve registry via ScopedRegistry (Project → Team → Org → PlatformConfig)
        from apps.deployments.models_registry_scope import ScopedRegistry

        scope_obj = getattr(service, 'project', None) or getattr(service, 'owner', None)
        registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj) if scope_obj else {}
        registry_url = (registry_info.get("url") or "").split("://")[-1] or None
        reg_username = registry_info.get("username") or ""
        reg_password = registry_info.get("password") or ""

        is_local = is_deployment_local(deployment)
        if not is_local and not registry_url:
            raise RuntimeError(
                "No registry URL configured. "
                "A registry is required to push/pull images for remote node deployments. "
                "Set a registry at the Project, Team, or Organization level, "
                "or configure CONTAINER_REGISTRY_URL."
            )
        if registry_url:
            remote_tag, _push_error = NixpacksBuilder.push_image(
                tag, registry_url, username=reg_username, password=reg_password,
            )
            pushed_to_registry = bool(remote_tag and remote_tag.startswith(registry_url))
            if not pushed_to_registry and not is_local:
                raise RuntimeError(
                    f"Image push failed: Local fallback is not allowed for remote deployments. "
                    f"Target node requires a working registry to pull {remote_tag}."
                )
            return remote_tag
        return tag

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)



def _build_uploaded_source(deployment, service) -> str:
    """Build an image from a previously uploaded zip archive."""
    build_dir = None
    try:
        deployment.status = Deployment.Status.BUILDING
        deployment.save(update_fields=["status"])
        broadcast_status(deployment)

        zip_path = _resolve_upload_zip_path(service.repository_url)
        build_dir = tempfile.mkdtemp(prefix=f"upload_{deployment.id}_")
        source_dir = os.path.join(build_dir, "source")
        os.makedirs(source_dir, exist_ok=True)

        append_log(deployment, f"Extracting uploaded source from {zip_path}...\n")
        _safe_extract_zip(zip_path, source_dir)
        log_exhaustive_deployment_diagnostics(deployment, service, source_dir)

        # Normalize archives that contain a single top-level folder.
        entries = [
            os.path.join(source_dir, item)
            for item in os.listdir(source_dir)
            if item not in ("__MACOSX",)
        ]
        if len(entries) == 1 and os.path.isdir(entries[0]):
            source_dir = entries[0]

        safe_service_name = _docker_safe_segment(service.name, fallback="upload")
        deploy_tag = str(deployment.id).replace("-", "")[:8]
        image_name = f"smsly/{safe_service_name}:{deploy_tag}"

        env_map = {env.key: env.value for env in service.env_vars.all()}
        dockerfile_path = os.path.join(source_dir, "Dockerfile")
        has_dockerfile = os.path.isfile(dockerfile_path)

        if service.buildpack == 'DOCKER':
            use_docker = True
        elif service.buildpack == 'NIXPACKS' or service.buildpack == 'STATIC':
            use_docker = False
        else:
            use_docker = has_dockerfile

        if use_docker:
            if not has_dockerfile:
                raise ValueError("Build strategy is docker but no Dockerfile was found.")
            append_log(deployment, "Building uploaded source with Dockerfile...\n")
            try:
                subprocess.run(
                    ["docker", "build", "-t", image_name, "--load", "-f", dockerfile_path, source_dir],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )
            except subprocess.CalledProcessError as exc:
                append_log(deployment, f"{exc.stdout or ''}\n{exc.stderr or ''}\n")
                raise
        else:
            if service.buildpack == 'STATIC':
                append_log(deployment, "Building uploaded source for Static Site (via Nixpacks)...\n")
            elif service.buildpack == 'NIXPACKS':
                append_log(deployment, "Building uploaded source with Nixpacks...\n")
            else:
                append_log(deployment, "Building uploaded source with Nixpacks fallback...\n")

            NixpacksBuilder.build_image(
                source_dir=source_dir,
                image_name=image_name,
                env_vars=env_map,
            )

        # ── Resolve registry via ScopedRegistry (Project → Team → Org → PlatformConfig)
        from apps.deployments.models_registry_scope import ScopedRegistry

        scope_obj = getattr(service, 'project', None) or getattr(service, 'owner', None)
        registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj) if scope_obj else {}
        registry_url = (registry_info.get("url") or "").split("://")[-1] or None
        reg_username = registry_info.get("username") or ""
        reg_password = registry_info.get("password") or ""

        is_local = is_deployment_local(deployment)
        if not is_local and not registry_url:
            raise RuntimeError(
                "No registry URL configured. "
                "A registry is required to push/pull images for remote node deployments. "
                "Set a registry at the Project, Team, or Organization level, "
                "or configure CONTAINER_REGISTRY_URL."
            )
        if registry_url:
            append_log(deployment, f"Pushing uploaded image to {registry_url}...\n")
            remote_tag, _push_error = NixpacksBuilder.push_image(
                image_name, registry_url, username=reg_username, password=reg_password,
            )
            pushed_to_registry = bool(remote_tag and remote_tag.startswith(registry_url))
            if not pushed_to_registry and not is_local:
                raise RuntimeError(
                    f"Image push failed: Local fallback is not allowed for remote deployments. "
                    f"Target node requires a working registry to pull {remote_tag}."
                )
            image_name = remote_tag
        return image_name

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)



def _resolve_upload_zip_path(repository_url: str) -> str:
    """Extract a local file path from file:// repository URLs."""
    parsed = urlparse(repository_url or "")
    if parsed.scheme != "file":
        raise ValueError("UPLOAD deploys require a file:// repository_url")

    if parsed.netloc and parsed.netloc not in ("localhost", "127.0.0.1"):
        raise ValueError("Only local file:// paths are supported for uploads")

    zip_path = unquote(parsed.path or "")
    if os.name == "nt" and zip_path.startswith("/"):
        # file:///C:/path.zip -> /C:/path.zip
        zip_path = zip_path.lstrip("/")
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Uploaded source archive not found: {zip_path}")
    return zip_path



def _safe_extract_zip(zip_path: str, destination: str):
    """Extract zip archive while preventing ZipSlip path traversal."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        dest_root = os.path.abspath(destination)
        for member in zf.infolist():
            member_name = member.filename
            if not member_name or member_name.endswith("/"):
                continue
            target_path = os.path.abspath(os.path.join(dest_root, member_name))
            if not target_path.startswith(dest_root + os.sep):
                raise ValueError("Archive contains unsafe file paths")
        zf.extractall(dest_root)
