"""Tests for remote UPLOAD deployments and tar.gz/zip extraction safety."""
import io
import os
import shutil
import tarfile
import tempfile
from contextlib import nullcontext
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, PlatformConfig, Service
from apps.deployments.models.servers import ManagedServer
from apps.deployments.tasks.deploy.build_nixpacks import _safe_extract_archive
from apps.deployments.tasks.deployment.tasks_deploy import (
    resume_deploy_task,
    smart_deploy_task,
)

User = get_user_model()


class RemoteUploadDeployTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="uploadremote",
            email="uploadremote@example.com",
            password="pw",
        )
        self.provider = CloudProvider.objects.create(
            name="uploadremote-prov",
            provider_type="LOCAL",
            is_active=True,
        )
        self.remote_server = ManagedServer.objects.create(
            name="remote-node-3",
            host="94.237.59.15",
            owner=self.user,
            api_token="tok-123",
            status="ONLINE",
            is_primary=False,
            allow_user_workloads=True,
        )
        PlatformConfig.objects.create(domain="trulay.site", server_ip="89.58.45.52")

    def test_master_builds_uploaded_source_before_remote_delegation(self):
        service = Service.objects.create(
            name="upload-remote-app",
            deploy_type="UPLOAD",
            repository_url="file:///app/media/uploads/app.zip",
            owner=self.user,
            provider=self.provider,
            server=self.remote_server,
        )
        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash="upload-abc123",
        )
        with patch(
            "apps.deployments.tasks.deployment.tasks_deploy.fleet_build_lock",
            return_value=nullcontext(),
        ), patch(
            "apps.deployments.tasks.deployment.tasks_deploy._build_uploaded_source",
            return_value="registry:5000/default/upload-remote-app:abc123",
        ) as mock_build, patch(
            "apps.deployments.tasks.deployment.tasks_deploy._handle_remote_deployment",
        ) as mock_remote:
            smart_deploy_task(str(deployment.id), str(self.provider.id), skip_review=True)

        mock_build.assert_called_once_with(deployment, service)
        mock_remote.assert_called_once()
        _, kwargs = mock_remote.call_args
        self.assertEqual(
            kwargs["image_name"],
            "registry:5000/default/upload-remote-app:abc123",
        )

    def test_node_reuses_prebuilt_image_for_delegated_upload_deploy(self):
        service = Service.objects.create(
            name="upload-node-app",
            deploy_type="UPLOAD",
            repository_url="file:///app/media/uploads/app.zip",
            docker_image="10.100.0.1:5000/default/upload-node-app:abc123",
            owner=self.user,
            provider=self.provider,
        )
        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash="upload-abc123",
            source_node="89.58.45.52",
        )
        with patch(
            "apps.deployments.tasks.deployment.tasks_deploy._build_uploaded_source",
        ) as mock_build, patch(
            "apps.deployments.tasks.deployment.tasks_deploy._deploy_container",
        ) as mock_deploy:
            smart_deploy_task(
                str(deployment.id),
                str(self.provider.id),
                skip_review=True,
                fast_deploy=True,
            )

        mock_build.assert_not_called()
        mock_deploy.assert_called_once_with(
            deployment,
            self.provider,
            "10.100.0.1:5000/default/upload-node-app:abc123",
            staged_only=False,
        )

    def test_resume_deploy_remote_upload_uses_upload_builder(self):
        service = Service.objects.create(
            name="upload-resume-app",
            deploy_type="UPLOAD",
            repository_url="file:///app/media/uploads/app.zip",
            owner=self.user,
            provider=self.provider,
            server=self.remote_server,
        )
        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash="upload-abc123",
        )
        with patch(
            "apps.deployments.tasks.deployment.tasks_deploy.fleet_build_lock",
            return_value=nullcontext(),
        ), patch(
            "apps.deployments.tasks.deployment.tasks_deploy._build_uploaded_source",
            return_value="registry:5000/default/upload-resume-app:abc123",
        ) as mock_build, patch(
            "apps.deployments.tasks.deployment.tasks_deploy.PipelineManager",
        ) as mock_pm, patch(
            "apps.deployments.tasks.deployment.tasks_deploy._handle_remote_deployment",
        ) as mock_remote:
            resume_deploy_task(str(deployment.id), str(self.provider.id))

        mock_pm.assert_not_called()
        mock_build.assert_called_once_with(deployment, service)
        mock_remote.assert_called_once_with(
            deployment,
            self.remote_server,
            image_name="registry:5000/default/upload-resume-app:abc123",
        )

    def test_safe_extract_archive_supports_targz_and_blocks_traversal(self):
        tmpdir = tempfile.mkdtemp(prefix="targz-test-")
        try:
            good_tgz = os.path.join(tmpdir, "good.tar.gz")
            out_dir = os.path.join(tmpdir, "out")
            os.makedirs(out_dir, exist_ok=True)
            with tarfile.open(good_tgz, "w:gz") as tf:
                payload = b"FROM python:3.11-slim\n"
                info = tarfile.TarInfo("Dockerfile")
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))

            _safe_extract_archive(good_tgz, out_dir)
            with open(os.path.join(out_dir, "Dockerfile"), "rb") as fh:
                self.assertEqual(fh.read(), b"FROM python:3.11-slim\n")

            bad_tgz = os.path.join(tmpdir, "bad.tar.gz")
            with tarfile.open(bad_tgz, "w:gz") as tf:
                payload = b"pwn"
                info = tarfile.TarInfo("../../evil.txt")
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))

            with self.assertRaises(ValueError):
                _safe_extract_archive(bad_tgz, out_dir)

            symlink_tgz = os.path.join(tmpdir, "sym.tar.gz")
            with tarfile.open(symlink_tgz, "w:gz") as tf:
                info = tarfile.TarInfo("escape")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)

            with self.assertRaises(ValueError):
                _safe_extract_archive(symlink_tgz, out_dir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_upload_service_file_uri_validation(self):
        svc = Service(
            name="upload-valid-uri",
            deploy_type="UPLOAD",
            repository_url="file:///app/media/uploads/app.zip",
            owner=self.user,
            provider=self.provider,
        )
        svc.full_clean()

        bad_svc = Service(
            name="upload-bad-uri",
            deploy_type="UPLOAD",
            repository_url="file://evil.example/app.zip",
            owner=self.user,
            provider=self.provider,
        )
        with self.assertRaises(ValidationError):
            bad_svc.full_clean()

        git_svc = Service(
            name="git-file-uri-blocked",
            deploy_type="GIT",
            repository_url="file:///app/media/uploads/app.zip",
            owner=self.user,
            provider=self.provider,
        )
        with self.assertRaises(ValidationError):
            git_svc.full_clean()

        too_long = Service(
            name="upload-long-uri",
            deploy_type="UPLOAD",
            repository_url="file:///app/" + ("a" * 210) + ".zip",
            owner=self.user,
            provider=self.provider,
        )
        with self.assertRaises(ValidationError):
            too_long.full_clean()

    def test_upload_deploy_api_saves_file_uri_and_queues_smart_deploy(self):
        import zipfile
        service = Service.objects.create(
            name="upload-api-remote",
            deploy_type="GIT",
            repository_url="https://github.com/example/repo",
            owner=self.user,
            provider=self.provider,
            server=self.remote_server,
        )
        tmpdir = tempfile.mkdtemp(prefix="upload-api-")
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("Dockerfile", "FROM python:3.11-slim\n")
            archive = SimpleUploadedFile(
                "app.zip", buf.getvalue(), content_type="application/zip"
            )
            client = APIClient()
            client.force_authenticate(user=self.user)
            with self.settings(MEDIA_ROOT=tmpdir), patch(
                "apps.deployments.views.files.smart_deploy_task.delay"
            ) as mock_delay:
                resp = client.post(
                    f"/api/v1/services/{service.id}/upload-deploy/",
                    {"file": archive},
                    format="multipart",
                )
            self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED, resp.data)
            service.refresh_from_db()
            self.assertEqual(service.deploy_type, "UPLOAD")
            self.assertTrue(service.repository_url.startswith("file://"))
            mock_delay.assert_called_once()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_attach_remote_scoped_network_ensures_missing_bridge(self):
        from unittest.mock import MagicMock
        from apps.addons.models import Addon
        from apps.addons.services.addon_provisioner import AddonProvisioner
        from apps.deployments.models.service import Project

        project = Project.objects.create(
            owner=self.user, name="RemoteScopeProj", slug="remote-scope-proj"
        )
        service = Service.objects.create(
            name="remote-addon-svc",
            owner=self.user,
            provider=self.provider,
            server=self.remote_server,
            project=project,
        )
        addon = Addon.objects.create(
            service=service,
            name="postgres-remote-addon-svc",
            addon_type="POSTGRES",
        )
        ssh = MagicMock()
        ssh.exec_command.return_value = ("", "", 0)
        with patch(
            "apps.deployments.models.network_scope.ScopedNetwork.resolve_network_config",
            return_value={"name": "smsly-net-scoped1", "allowed_egress_networks": []},
        ), patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator",
        ) as mock_orch:
            AddonProvisioner()._attach_remote_scoped_network(
                ssh,
                self.remote_server,
                addon,
                "smsly-addon-postgres-1",
                "postgres-remote-addon-svc",
            )

        mock_orch.return_value.ensure_remote_network.assert_called_once()
        cmds = [c.args[0] for c in ssh.exec_command.call_args_list]
        self.assertTrue(any("docker network create --driver bridge" in c for c in cmds))
        self.assertTrue(any("docker network connect --alias" in c for c in cmds))

    def test_push_built_image_strips_mismatched_master_registry_prefix(self):
        from unittest.mock import MagicMock
        from apps.deployments.tasks.deploy.build_nixpacks import _push_built_image

        service = Service.objects.create(
            name="upload-push-strip",
            deploy_type="UPLOAD",
            repository_url="file:///app/media/uploads/app.zip",
            owner=self.user,
            provider=self.provider,
            server=self.remote_server,
        )
        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.BUILDING,
            commit_hash="upload-push123",
        )
        mock_img = MagicMock()
        mock_docker = MagicMock()
        mock_docker.images.get.return_value = mock_img

        with self.settings(CONTAINER_REGISTRY_URL="registry:5000"), patch.dict(
            os.environ, {"WIREGUARD_MASTER_MESH_IP": "10.100.0.1"}
        ), patch(
            "apps.deployments.models.registry_scope.ScopedRegistry.resolve_registry_credentials",
            return_value={"url": "registry:5000", "username": "u", "password": "p"},
        ), patch(
            "apps.cloud.docker_client.get_docker_client",
            return_value=mock_docker,
        ), patch(
            "apps.deployments.tasks.deploy.build_nixpacks.NixpacksBuilder.push_image",
            return_value=("registry:5000/default/upload-push-strip:tag1", None),
        ) as mock_push:
            out = _push_built_image(
                deployment,
                service,
                "10.100.0.1:5000/default/upload-push-strip:tag1",
                is_local=False,
            )

        self.assertEqual(out, "registry:5000/default/upload-push-strip:tag1")
        mock_img.tag.assert_called_once_with("default/upload-push-strip:tag1")
        mock_push.assert_called_once_with(
            "default/upload-push-strip:tag1",
            "registry:5000",
            username="u",
            password="p",
        )
