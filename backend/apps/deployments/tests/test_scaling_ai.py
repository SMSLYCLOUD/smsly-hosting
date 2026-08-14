# pylint: disable=invalid-name
"""
Regression tests for the deterministic scaling-decision engine.

Covers:
  * The ``SCALE_DOWN_CPU`` constant must be defined (regression).
  * The decision engine does not raise ``NameError`` during scale-down.
  * When CPU is below the threshold and a RUNNING replica exists, the
    recommendation is ``scale_down`` (assuming cooldown is satisfied).
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class ScalingAIDecisionTests(TestCase):
    """Direct test of the ScalingAnalyzer._decide() decision engine."""

    def setUp(self):
        from apps.deployments.models import Project, Service
        from apps.autoscaler.services.scaling_ai import (
            SCALE_DOWN_CPU,
            ScalingAnalyzer,
        )

        self.user = User.objects.create_user(username="scale-ai", password="x")
        self.project = Project.objects.create(name="S", owner=self.user)
        self.service = Service.objects.create(
            name="scaling-ai-svc",
            owner=self.user,
            project=self.project,
        )
        self.SCALE_DOWN_CPU = SCALE_DOWN_CPU
        self.ScalingAnalyzer = ScalingAnalyzer

    def test_scale_down_cpu_constant_defined(self):
        # Regression: the constant used to be missing, causing NameError.
        from apps.autoscaler.services import scaling_ai

        self.assertTrue(
            hasattr(scaling_ai, "SCALE_DOWN_CPU"),
            "SCALE_DOWN_CPU must be defined in scaling_ai.py",
        )
        self.assertIsInstance(self.SCALE_DOWN_CPU, (int, float))
        # Sanity: scale-down threshold should be lower than the high threshold.
        from apps.autoscaler.services.scaling_ai import CPU_HIGH

        self.assertLess(
            self.SCALE_DOWN_CPU,
            CPU_HIGH,
            "SCALE_DOWN_CPU must be lower than CPU_HIGH",
        )

    @patch("apps.autoscaler.services.scaling_ai.requests.get")
    def test_scale_down_decision_does_not_raise_nameerror(self, mock_get):
        from apps.deployments.models.replica import ServiceReplica
        from apps.autoscaler.services.scaling_ai import (
            CPU_LOW,
        )

        ServiceReplica.objects.create(
            service=self.service,
            container_name="rep-1",
            status="RUNNING",
        )
        ServiceReplica.objects.create(
            service=self.service,
            container_name="rep-2",
            status="RUNNING",
        )

        # Mock prometheus + loki calls to return empty results
        class _Resp:
            ok = True

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": {"result": []}}

        mock_get.return_value = _Resp()

        analyzer = self.ScalingAnalyzer(self.service)
        metrics = {"cpu_percent": float(CPU_LOW) - 1.0, "memory_mb": 100.0, "memory_trend": 0.0}
        errors = {"error_count_1h": 0, "oom_detected": False, "crash_loop": False, "has_errors": False}
        # The unified DecisionEngine only scales down when running_replicas
        # exceeds min_replicas (default 1), so use 2 running replicas.
        guardrails = {
            "running_replicas": 2,
            "max_replicas": 5,
            "at_capacity": False,
            "spawning_in_progress": False,
            "cooldown_active": False,
            "cooldown_down_active": False,
            "can_scale_up": True,
            "can_scale_down": True,
        }
        try:
            decision = analyzer._decide(metrics, errors, guardrails)
        except NameError as exc:
            self.fail(f"_decide raised NameError: {exc}")
        self.assertEqual(decision["action"], "scale_down")
        self.assertEqual(decision["scale_up_by"], 0)
