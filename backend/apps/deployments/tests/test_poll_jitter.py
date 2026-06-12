import pytest
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.deployments.models import Deployment, Service
from apps.deployments import tasks


def _make_deployment(user):
    service = Service.objects.create(
        owner=user,
        name="poll-jitter-svc",
        deploy_type="GIT",
    )
    return Deployment.objects.create(
        service=service,
        commit_hash="deadbeef",
    )


@pytest.mark.django_db(transaction=True)
class PollJitterTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="poll_jitter", password="123"
        )
        self.deployment = _make_deployment(self.user)
        self.orchestrator = MagicMock()
        self.orchestrator.server.host = "203.0.113.80"
        self.orchestrator.poll_deployment.return_value = {
            "status": Deployment.Status.BUILDING,
        }

    def tearDown(self):
        self.deployment.delete()
        self.deployment.service.delete()
        self.user.delete()

    def test_single_iteration_sleep_within_jitter_range(self):
        sleep_values = []

        def fake_sleep(value):
            sleep_values.append(value)
            raise StopIteration("stop after first sleep")

        with patch.object(tasks.time, "sleep", side_effect=fake_sleep):
            try:
                tasks._poll_remote_deployment(
                    self.deployment,
                    self.orchestrator,
                    "remote-dep-id-1",
                )
            except StopIteration:
                pass

        self.assertTrue(len(sleep_values) >= 1,
                        f"Expected at least 1 sleep, got {len(sleep_values)}")
        first = sleep_values[0]
        self.assertGreaterEqual(first, 10.0)
        self.assertLessEqual(first, 12.0)

    def test_multiple_iterations_each_get_distinct_sleep_in_range(self):
        sleep_values = []
        max_iterations = 5

        def fake_sleep(value):
            sleep_values.append(value)
            if len(sleep_values) >= max_iterations:
                raise StopIteration("stop after max_iterations sleeps")
            return None

        with patch.object(tasks.time, "sleep", side_effect=fake_sleep):
            try:
                tasks._poll_remote_deployment(
                    self.deployment,
                    self.orchestrator,
                    "remote-dep-id-2",
                )
            except StopIteration:
                pass

        self.assertGreaterEqual(len(sleep_values), 2,
                                f"Expected at least 2 sleeps, got {len(sleep_values)}")
        for v in sleep_values:
            self.assertGreaterEqual(v, 10.0, f"Sleep {v} below 10.0")
            self.assertLessEqual(v, 12.0, f"Sleep {v} above 12.0")
        self.assertGreater(
            len(set(sleep_values)), 1,
            f"Expected at least 2 distinct sleep values, got {sleep_values}",
        )
