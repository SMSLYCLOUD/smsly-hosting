# pylint: disable=invalid-name
"""
Tests for deployment serializers.
Covers: ServiceSerializer, ServiceListSerializer, DeploymentSerializer,
        EnvVarSerializer.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.deployments.models.core import (
    Deployment,
    EnvironmentVariable,
    Service,
)
from apps.deployments.serializers.deployment import DeploymentSerializer
from apps.deployments.serializers.service import (
    EnvVarSerializer,
    ServiceListSerializer,
    ServiceSerializer,
)

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "serializer-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
class ServiceSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ser", email="ser@test.com", password="pass1234"
        )
        self.service = Service.objects.create(
            name="my-app",
            owner=self.user,
            deploy_type="GIT",
            buildpack="NIXPACKS",
            repository_url="https://github.com/test/app",
            internal_port=8000,
        )

    def test_serialization_output_structure(self):
        data = ServiceSerializer(self.service).data
        self.assertEqual(str(data["id"]), str(self.service.id))
        self.assertEqual(data["name"], "my-app")
        for field in ("created_at", "updated_at", "env_vars", "server_id",
                       "latest_deployment", "service_url", "estimated_cost",
                       "node_metadata"):
            self.assertIn(field, data)

    def test_read_only_fields_not_writable(self):
        read_only = ServiceSerializer.Meta.read_only_fields
        for field in ("id", "created_at", "updated_at", "owner", "server"):
            self.assertIn(field, read_only)

    def test_service_url_uses_public_domain(self):
        self.service.public_domain = "app.example.com"
        self.service.public_domain_hidden = False
        self.service.save()
        data = ServiceSerializer(self.service).data
        self.assertEqual(data["service_url"], "https://app.example.com")

    def test_service_url_fallback_when_domain_hidden(self):
        self.service.public_domain = "app.example.com"
        self.service.public_domain_hidden = True
        self.service.save()
        data = ServiceSerializer(self.service).data
        self.assertIn("https://", data["service_url"])
        self.assertNotIn("app.example.com", data["service_url"])

    def test_create_with_valid_data_and_env_vars(self):
        payload = {
            "name": "new-service",
            "deploy_type": "DOCKER",
            "docker_image": "nginx:latest",
            "env_vars": [
                {"key": "PORT", "value": "3000", "is_secret": False},
                {"key": "SECRET_KEY", "value": "s3cr3t", "is_secret": True},
            ],
        }
        serializer = ServiceSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        service = serializer.save(owner=self.user)
        self.assertEqual(service.env_vars.count(), 2)
        self.assertTrue(service.env_vars.get(key="SECRET_KEY").is_secret)

    def test_name_validation_strips_and_slugs(self):
        serializer = ServiceSerializer(data={"name": "  My  Cool  App!  "})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["name"], "my-cool-app")

    def test_name_validation_rejects_only_special_chars(self):
        serializer = ServiceSerializer(data={"name": "---"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)


@override_settings(CACHES=TEST_CACHES)
class ServiceListSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="listuser", email="list@test.com", password="pass1234"
        )
        self.service = Service.objects.create(name="list-app", owner=self.user)

    def test_serialization_has_expected_fields(self):
        data = ServiceListSerializer(self.service).data
        expected = {
            "id", "name", "status", "owner", "project",
            "server", "public_domain", "custom_domains", "internal_port",
            "health_status", "deploy_type", "buildpack", "created_at",
            "updated_at", "latest_deployment", "node_metadata",
        }
        self.assertEqual(set(data.keys()), expected)

    def test_latest_deployment_none_when_no_deployments(self):
        self.assertIsNone(ServiceListSerializer(self.service).data["latest_deployment"])

    def test_latest_deployment_present_when_deployments_exist(self):
        dep = Deployment.objects.create(
            service=self.service, commit_hash="abc123",
            status=Deployment.Status.ACTIVE,
        )
        latest = ServiceListSerializer(self.service).data["latest_deployment"]
        self.assertEqual(latest["id"], str(dep.id))
        self.assertEqual(latest["status"], "ACTIVE")

    def test_node_metadata_defaults_when_no_server(self):
        meta = ServiceListSerializer(self.service).data["node_metadata"]
        self.assertEqual(meta["id"], "local")
        self.assertEqual(meta["target_type"], "Local")


@override_settings(CACHES=TEST_CACHES)
class DeploymentSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="depuser", email="dep@test.com", password="pass1234"
        )
        self.service = Service.objects.create(name="dep-svc", owner=self.user)
        self.deployment = Deployment.objects.create(
            service=self.service,
            commit_hash="deadbeef",
            commit_message="Initial commit",
            branch="main",
            status=Deployment.Status.ACTIVE,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )

    def test_serialization_output_structure(self):
        data = DeploymentSerializer(self.deployment).data
        self.assertEqual(data["commit_hash"], "deadbeef")
        self.assertEqual(data["service_name"], "dep-svc")
        self.assertEqual(data["status"], "ACTIVE")

    def test_duration_seconds_computed(self):
        data = DeploymentSerializer(self.deployment).data
        self.assertIsInstance(data["duration_seconds"], float)
        self.assertGreaterEqual(data["duration_seconds"], 0)

    def test_duration_seconds_none_when_no_times(self):
        dep = Deployment.objects.create(
            service=self.service, commit_hash="aa",
            status=Deployment.Status.QUEUED,
        )
        self.assertIsNone(DeploymentSerializer(dep).data["duration_seconds"])

    def test_read_only_fields_not_writable(self):
        read_only = DeploymentSerializer.Meta.read_only_fields
        for field in ("id", "container_id", "started_at", "finished_at",
                       "created_at", "target_server"):
            self.assertIn(field, read_only)


@override_settings(CACHES=TEST_CACHES)
class EnvVarSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="envuser", email="env@test.com", password="pass1234"
        )
        self.service = Service.objects.create(name="env-svc", owner=self.user)

    def test_secret_value_masked_by_default(self):
        var = EnvironmentVariable.objects.create(
            service=self.service, key="DB_PASS", value="supersecret",
            is_secret=True,
        )
        self.assertEqual(EnvVarSerializer(var).data["value"], "********")

    def test_secret_value_revealed_with_context(self):
        var = EnvironmentVariable.objects.create(
            service=self.service, key="DB_PASS", value="supersecret",
            is_secret=True,
        )
        data = EnvVarSerializer(var, context={"reveal_secrets": True}).data
        self.assertEqual(data["value"], "supersecret")

    def test_create_valid_data(self):
        serializer = EnvVarSerializer(
            data={"key": "API_KEY", "value": "key123", "is_secret": False}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        var = serializer.save(service=self.service)
        self.assertEqual(var.key, "API_KEY")
        self.assertEqual(var.value, "key123")

    def test_update_value(self):
        var = EnvironmentVariable.objects.create(
            service=self.service, key="PORT", value="8000",
        )
        serializer = EnvVarSerializer(
            var, data={"key": "PORT", "value": "9000"}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.save().value, "9000")

    def test_empty_key_rejected(self):
        serializer = EnvVarSerializer(data={"key": "", "value": "val"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("key", serializer.errors)
