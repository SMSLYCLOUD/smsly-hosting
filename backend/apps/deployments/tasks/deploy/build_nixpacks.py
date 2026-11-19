from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from urllib.parse import unquote, urlparse

from django.conf import settings

from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.constants import DOCKER_BUILD_TIMEOUT
from apps.deployments.models import Deployment, Service
from apps.deployments.utils import append_log, broadcast_status, is_deployment_local, update_stage

from .build_docker import _docker_safe_segment

logger = logging.getLogger(__name__)

def _resolve_upload_zip_path(repository_url: str) -> str:
    parsed = urlparse(repository_url or "")
    if parsed.scheme != "file":
        raise ValueError("UPLOAD deploys require a file:// repository_url")

    if parsed.netloc and parsed.netloc not in ("localhost", "127.0.0.1"):
        raise ValueError("Only local file:// paths are supported for uploads")

    zip_path = unquote(parsed.path or "")
    if os.name == "nt" and zip_path.startswith("/"):
        zip_path = zip_path.lstrip("/")
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Uploaded source archive not found: {zip_path}")
    return zip_path

def _safe_extract_zip(zip_path: str, destination: str):
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

def _build_function(deployment, service) -> str:
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
        from apps.deployments.services.pipeline.build import _with_local_registry
        tag = _with_local_registry(
            f"smsly/func-{safe_service_name}:{deploy_tag}", deployment
        )

        append_log(deployment, f"Building function {tag}...\n")

        from apps.deployments.services.pipeline.build import (
            _build_limits,
            _create_ephemeral_builder,
            _remove_ephemeral_builder,
        )
        import uuid
        try:
            mem_mb, cpu_pct, _ = _build_limits()
        except Exception:
            mem_mb, cpu_pct = 10240, 400
        eph_builder = _create_ephemeral_builder(
            f"smsly-eph-{uuid.uuid4().hex}", mem_mb, cpu_pct
        )
        if eph_builder:
            cmd = [
                "docker", "buildx", "build",
                "--builder", eph_builder,
                "--output", "type=docker",
                "-t", tag,
                build_dir,
            ]
        else:
            cmd = ["docker", "build", "-t", tag, "--load", build_dir]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=DOCKER_BUILD_TIMEOUT,
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
        finally:
            if eph_builder:
                _remove_ephemeral_builder(eph_builder)

        update_stage(deployment, 'Build', 'success')
        is_local = is_deployment_local(deployment)
        return _push_built_image(deployment, service, tag, is_local=is_local)

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)

def _safe_extract_archive(archive_path: str, destination: str):
    """Extract a .zip or .tar.gz/.tgz source archive while blocking traversal."""
    lower = archive_path.lower()
    if lower.endswith('.tar.gz') or lower.endswith('.tgz'):
        dest_root = os.path.abspath(destination)
        with tarfile.open(archive_path, "r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                if not (member.isreg() or member.isdir()):
                    raise ValueError(f"Archive contains unsafe non-file entry: {member.name}")
                target_path = os.path.abspath(os.path.join(dest_root, member.name))
                if target_path != dest_root and not target_path.startswith(dest_root + os.sep):
                    raise ValueError(f"Archive contains unsafe file paths: {member.name}")
            tf.extractall(dest_root, members=members)
        return
    _safe_extract_zip(archive_path, destination)


def _push_built_image(deployment, service, image_name: str, is_local: bool) -> str:
    """Resolve registry credentials and push *image_name* when configured."""
    registry = getattr(settings, "CONTAINER_REGISTRY_URL", None)
    username = None
    password = None
    try:
        from apps.deployments.models.registry_scope import ScopedRegistry
        creds = ScopedRegistry.resolve_registry_credentials(getattr(service, "project", None))
        scoped_url = (creds.get("url") or "").strip()
        if scoped_url:
            registry = scoped_url
        username = creds.get("username") or None
        password = creds.get("password") or None
    except Exception:
        pass

    if not is_local and not registry:
        raise RuntimeError(
            "CONTAINER_REGISTRY_URL is not configured. "
            "A registry is required to push/pull images for remote node deployments."
        )
    if not registry:
        return image_name

    append_log(deployment, f"Pushing uploaded image to {registry}...\n")
    # Retag master-mesh-qualified refs (e.g. 10.100.0.1:5000/ns/app:tag) down
    # to bare ns/app:tag before pushing to a different registry host like
    # registry:5000; otherwise push_image stacks both hosts into an invalid ref.
    push_ref = image_name
    try:
        from apps.deployments.services.registry_routing import (
            _split_ref,
            is_master_registry_ref,
        )
        _phost, _prest = _split_ref(image_name or "")
        if _phost and _prest and is_master_registry_ref(image_name or ""):
            _norm_target = (registry or "").split("://")[-1].rstrip("/")
            if _norm_target and _phost != _norm_target:
                from apps.cloud.docker_client import get_docker_client
                get_docker_client().images.get(image_name).tag(_prest)
                push_ref = _prest
    except Exception as exc:
        logger.warning("Registry prefix strip failed for %s: %s", image_name, exc)

    remote_tag, push_error = NixpacksBuilder.push_image(
        push_ref,
        registry,
        username=username,
        password=password,
    )
    registry_host = registry.split("://", 1)[-1].rstrip("/")
    pushed_to_registry = bool(
        not push_error
        and remote_tag
        and (remote_tag.startswith(registry) or remote_tag.startswith(registry_host))
    )
    if not pushed_to_registry and not is_local:
        detail = f" ({push_error})" if push_error else ""
        raise RuntimeError(
            f"Image push failed{detail}: Local fallback is not allowed for remote deployments. "
            f"Target node requires a working registry to pull {remote_tag}."
        )
    return remote_tag


def _build_uploaded_source(deployment, service) -> str:
    build_dir = None
    try:
        deployment.status = Deployment.Status.BUILDING
        deployment.save(update_fields=["status"])
        broadcast_status(deployment)
        update_stage(deployment, 'Build', 'running')

        archive_path = _resolve_upload_zip_path(service.repository_url)
        build_dir = tempfile.mkdtemp(prefix=f"upload_{deployment.id}_")
        source_dir = os.path.join(build_dir, "source")
        os.makedirs(source_dir, exist_ok=True)

        append_log(deployment, f"Extracting uploaded source from {archive_path}...\n")
        _safe_extract_archive(archive_path, source_dir)

        entries = [
            os.path.join(source_dir, item)
            for item in os.listdir(source_dir)
            if item not in ("__MACOSX",)
        ]
        if len(entries) == 1 and os.path.isdir(entries[0]):
            source_dir = entries[0]

        safe_service_name = _docker_safe_segment(service.name, fallback="upload")
        deploy_tag = str(deployment.id).replace("-", "")[:8]
        from apps.deployments.services.registry_credentials import (
            project_image_namespace,
        )
        from apps.deployments.services.pipeline.build import _with_local_registry
        image_name = _with_local_registry(
            f"{project_image_namespace(service)}/{safe_service_name}:{deploy_tag}",
            deployment,
        )

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
            from apps.deployments.services.pipeline.build import (
                _build_limits,
                _create_ephemeral_builder,
                _remove_ephemeral_builder,
            )
            import uuid
            try:
                mem_mb, cpu_pct, _ = _build_limits()
            except Exception:
                mem_mb, cpu_pct = 10240, 400
            eph_builder = _create_ephemeral_builder(
                f"smsly-eph-{uuid.uuid4().hex}", mem_mb, cpu_pct
            )
            try:
                if eph_builder:
                    build_cmd = [
                        "docker", "buildx", "build",
                        "--builder", eph_builder,
                        "--output", "type=docker",
                        "-t", image_name,
                        "-f", dockerfile_path,
                        source_dir,
                    ]
                else:
                    build_cmd = [
                        "docker", "build", "-t", image_name, "--load", "-f", dockerfile_path, source_dir,
                    ]
                result = subprocess.run(
                    build_cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_BUILD_TIMEOUT,
                )
                build_output = "\n".join(
                    part for part in [result.stdout, result.stderr] if part
                ).strip()
                if build_output:
                    append_log(deployment, f"{build_output[-4000:]}\n")
            except subprocess.CalledProcessError as exc:
                append_log(deployment, f"{exc.stdout or ''}\n{exc.stderr or ''}\n")
                raise
            finally:
                if eph_builder:
                    _remove_ephemeral_builder(eph_builder)
        else:
            if service.buildpack == 'STATIC':
                append_log(deployment, "Building uploaded source for Static Site (via Nixpacks)...\n")
            elif service.buildpack == 'NIXPACKS':
                append_log(deployment, "Building uploaded source with Nixpacks...\n")
            else:
                append_log(deployment, "Building uploaded source with Nixpacks fallback...\n")

            from apps.deployments.services.pipeline.build import (
                _build_limits,
                _create_ephemeral_builder,
                _remove_ephemeral_builder,
            )
            import uuid
            try:
                mem_mb, cpu_pct, _ = _build_limits()
            except Exception:
                mem_mb, cpu_pct = 10240, 400
            eph_builder = _create_ephemeral_builder(
                f"smsly-eph-{uuid.uuid4().hex}", mem_mb, cpu_pct
            )
            try:
                NixpacksBuilder.build_image(
                    source_dir=source_dir,
                    image_name=image_name,
                    env_vars=env_map,
                    builder_name=eph_builder,
                )
            finally:
                if eph_builder:
                    _remove_ephemeral_builder(eph_builder)

        update_stage(deployment, 'Build', 'success')
        is_local = is_deployment_local(deployment)
        return _push_built_image(deployment, service, image_name, is_local=is_local)

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)
