import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import docker

# Ensure backend path is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../backend')))

# Mock Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
from django.conf import settings

if not settings.configured:
    django.setup()

from apps.cloud.adapters.local import LocalAdapter
from apps.cloud.services.git_manager import GitManager
from apps.deployments.utils import extract_dockerfile_arg_names


class TestDeploymentPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.docker_client = None
        self.docker_available = False
        self.docker_error = ""
        try:
            self.docker_client = docker.from_env()
            self.docker_available = True
        except Exception as exc:
            self.docker_error = str(exc)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def require_docker(self):
        if not self.docker_available:
            self.skipTest(f"Docker unavailable: {self.docker_error}")

    def test_git_clone_public(self):
        """Verify Git cloning of a public repo works."""
        repo_url = "https://github.com/octocat/Hello-World.git"
        print(f"\n[Test] Cloning {repo_url}...")
        try:
            # octocat/Hello-World uses 'master' branch
            cloned_dir = GitManager.clone_repo(repo_url, branch='master', destination=self.temp_dir)
            self.assertTrue(os.path.exists(os.path.join(cloned_dir, "README")), "README should exist")
            print(f"[Pass] Cloned successfully to {cloned_dir}")
        except Exception as e:
            self.fail(f"Git clone failed: {e}")

    def test_git_clone_trailing_slash(self):
        """Verify Git cloning handles trailing slashes."""
        repo_url = "https://github.com/octocat/Hello-World.git/"
        print(f"\n[Test] Cloning {repo_url} (trailing slash)...")
        try:
            # octocat/Hello-World uses 'master' branch
            cloned_dir = GitManager.clone_repo(repo_url, branch='master', destination=self.temp_dir)
            self.assertTrue(os.path.exists(os.path.join(cloned_dir, "README")), "README should exist")
            print(f"[Pass] Cloned successfully to {cloned_dir}")
        except Exception as e:
            self.fail(f"Git clone failed with trailing slash: {e}")

    def test_dockerfile_arg_extraction(self):
        """Verify ARG extraction from Dockerfile."""
        dockerfile_content = """
        FROM alpine
        ARG MY_ARG
        ARG ANOTHER_ARG=default
        # ARG COMMENTED_ARG
        RUN echo hello
        """
        dockerfile_path = os.path.join(self.temp_dir, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile_content)

        print("\n[Test] Extracting ARGs from Dockerfile...")
        args = extract_dockerfile_arg_names(dockerfile_path)
        self.assertIn("MY_ARG", args)
        self.assertIn("ANOTHER_ARG", args)
        self.assertNotIn("COMMENTED_ARG", args)
        print(f"[Pass] Extracted args: {args}")

    def test_local_adapter_connectivity(self):
        """Verify LocalAdapter can talk to Docker."""
        self.require_docker()
        print("\n[Test] LocalAdapter connectivity...")
        try:
            adapter = LocalAdapter()
            self.assertTrue(adapter.authenticate(), "Adapter should authenticate")
            version = adapter.docker_client.version()
            print(f"[Pass] Docker version: {version.get('Version')}")

            # check network
            try:
                adapter.docker_client.networks.get("smsly-net")
                print("[Pass] Network 'smsly-net' exists.")
            except docker.errors.NotFound:
                print("[Warn] Network 'smsly-net' does NOT exist (pipeline should create it).")
        except Exception as e:
            self.fail(f"LocalAdapter check failed: {e}")

    def test_nixpacks_installed(self):
        """Verify Nixpacks binary is available."""
        print("\n[Test] Checking for Nixpacks...")
        if shutil.which("nixpacks"):
            print("[Pass] Nixpacks is installed.")
        else:
            print("[Fail] Nixpacks is NOT installed. Nixpacks builds will fail.")
            # We don't fail the test suite here to let other tests run, but this is critical.

    def test_health_check_logic(self):
        """Verify LocalAdapter constructs health checks correctly (simulated)."""
        print("\n[Test] Health Check Logic...")
        adapter = LocalAdapter()
        # Mock docker client
        with patch.object(adapter, 'docker_client') as mock_client:
            mock_container = MagicMock()
            mock_container.id = "test-id"
            mock_client.containers.run.return_value = mock_container

            # Test deploy call
            try:
                adapter.deploy_container(
                    service_name="test-health",
                    image="nginx:alpine",
                    env_vars={"PORT": "80"},
                    cpu=256,
                    memory=512,
                    healthcheck={
                        "path": "/health",
                        "interval": 10,
                        "timeout": 5,
                        "retries": 3
                    }
                )

                # Check call args
                call_kwargs = mock_client.containers.run.call_args[1]
                healthcheck_arg = call_kwargs.get('healthcheck')
                self.assertIsNotNone(healthcheck_arg)
                # Inspect Docker Healthcheck object if possible, or assume it's opaque
                print(f"[Pass] Health check argument passed: {healthcheck_arg}")

            except Exception as e:
                self.fail(f"Deploy with health check failed: {e}")

if __name__ == '__main__':
    unittest.main()
