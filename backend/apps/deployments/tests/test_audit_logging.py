from django.test import TestCase
from unittest.mock import patch
from apps.deployments.models import Service, Deployment, Project
from apps.deployments.utils import append_log
from django.contrib.auth import get_user_model

User = get_user_model()

class AuditLoggingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="log_user", password="pwd")
        self.project = Project.objects.create(name="Log Proj", owner=self.user)
        self.service = Service.objects.create(
            name="log-service",
            owner=self.user,
            project=self.project,
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            status='STAGED',
            commit_hash='abc1234',
        )

    def test_append_log_redacts_secrets(self):
        append_log(self.deployment, "Connecting to postgresql://user:super_secret_pwd@localhost/db")
        self.deployment.refresh_from_db()
        self.assertIn("postgresql://user:***@localhost/db", self.deployment.build_logs)
        self.assertNotIn("super_secret_pwd", self.deployment.build_logs)

        append_log(self.deployment, "Connecting to redis://:super_secret_redis@localhost:6379")
        self.deployment.refresh_from_db()
        self.assertIn("redis://:***@localhost:6379", self.deployment.build_logs)
        self.assertNotIn("super_secret_redis", self.deployment.build_logs)

        append_log(self.deployment, "Exporting API_KEY=my-secret-token")
        self.deployment.refresh_from_db()
        self.assertIn("API_KEY=***", self.deployment.build_logs)
        self.assertNotIn("my-secret-token", self.deployment.build_logs)
