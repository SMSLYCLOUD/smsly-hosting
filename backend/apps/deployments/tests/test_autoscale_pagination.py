# pylint: disable=invalid-name
"""Tests for cursor-based pagination in analyze_all_services_task."""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.autoscaler.services.tasks_autoscale import (
    AUTOSCALE_BATCH_SIZE,
    analyze_all_services_task,
)

User = get_user_model()


class AutoscalePaginationTests(TestCase):
    """The task must paginate through ALL eligible services, processing
    at most ``AUTOSCALE_BATCH_SIZE`` per page, and not silently drop
    overflow when the fleet exceeds the batch size.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="page-user", password="pwd")
        self.project = Project.objects.create(name="P", owner=self.user)

    def _make_services(self, count: int, name_prefix: str = "svc") -> list:
        """Create services that match the task's filter.

        The task filters on ``status='RUNNING'`` (a ServiceReplica value
        that was historically used on Service). We bypass ``save()``'s
        ``full_clean`` via ``bulk_create`` so we can set the raw value
        the production code looks for.
        """
        services = []
        for i in range(count):
            services.append(
                Service(
                    id=uuid.uuid4(),
                    name=f"{name_prefix}-{i:03d}",
                    slug=f"{name_prefix}-slug-{i:03d}",
                    owner=self.user,
                    project=self.project,
                    status="RUNNING",
                    deploy_mode="SINGLE",
                    compose_file="",
                )
            )
        Service.objects.bulk_create(services)
        return list(
            Service.objects.filter(name__startswith=name_prefix).order_by("id")
        )

    @patch("apps.autoscaler.services.tasks_autoscale.analyze_and_scale_service")
    def test_processes_all_50_services_across_batches(self, mock_analyze):
        services = self._make_services(50)

        result = analyze_all_services_task()

        self.assertEqual(mock_analyze.call_count, 50)
        self.assertEqual(result["analyzed"], 50)
        analyzed_ids = {call.args[0] for call in mock_analyze.call_args_list}
        self.assertEqual(analyzed_ids, {str(s.id) for s in services})

    @patch("apps.autoscaler.services.tasks_autoscale.analyze_and_scale_service")
    def test_each_iteration_respects_batch_size(self, mock_analyze):
        self._make_services(AUTOSCALE_BATCH_SIZE * 3)

        result = analyze_all_services_task()

        self.assertEqual(mock_analyze.call_count, AUTOSCALE_BATCH_SIZE * 3)
        self.assertEqual(result["analyzed"], AUTOSCALE_BATCH_SIZE * 3)

    @patch("apps.autoscaler.services.tasks_autoscale.analyze_and_scale_service")
    def test_last_id_cursor_advances(self, mock_analyze):
        services = self._make_services(AUTOSCALE_BATCH_SIZE + 5)

        analyze_all_services_task()

        expected_order = [str(s.id) for s in sorted(services, key=lambda s: str(s.id))]
        actual_order = [call.args[0] for call in mock_analyze.call_args_list]
        self.assertEqual(actual_order, expected_order)

    @patch("apps.autoscaler.services.tasks_autoscale.analyze_and_scale_service")
    def test_empty_services_returns_zero(self, mock_analyze):
        result = analyze_all_services_task()
        self.assertEqual(result["analyzed"], 0)
        mock_analyze.assert_not_called()

    @patch("apps.autoscaler.services.tasks_autoscale.analyze_and_scale_service")
    def test_skips_failing_service_and_continues(self, mock_analyze):
        services = self._make_services(25)
        target_id = str(services[4].id)

        def side_effect(sid):
            if sid == target_id:
                raise RuntimeError("boom")

        mock_analyze.side_effect = side_effect

        result = analyze_all_services_task()
        self.assertEqual(mock_analyze.call_count, 25)
        self.assertEqual(result["analyzed"], 24)


