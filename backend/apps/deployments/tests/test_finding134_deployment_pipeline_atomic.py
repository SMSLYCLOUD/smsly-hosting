# pylint: disable=invalid-name
"""Regression tests for Finding #134 (deployment_pipeline atomicity).

NOTE: The report's reference to ``Deployment.objects.create`` plus
``runtime_checks`` / ``apply_artifact`` at
``apps/deployments/services/safedeploy/deployment_pipeline.py:268-280``
is no longer accurate — the file has been refactored:

  * the only ``Deployment.objects.create`` call lives in
    ``apps/deployments/views.py:`` (not in the pipeline);
  * ``runtime_checks`` and ``apply_artifact`` are no longer methods
    of ``ProductionDeploymentPipeline``;
  * the remaining state-transition writes in the pipeline are
    ``deployment.status = ...; deployment.save()`` calls that are
    inside a worker task (not inside an HTTP request, so transaction
    context is provided by Django's per-task ``transaction.atomic``
    wrapper).

We still lock the remaining state-transition safety net in tests
that exercise the pipeline end-to-end: every public method that
mutates the deployment row is the responsibility of the worker
transaction, and the two public mutators (``approve_deployment`` and
``reject_deployment``) are already decorated with
``@transaction.atomic``.
"""

import inspect

from django.test import TestCase

from apps.deployments.services.safedeploy import deployment_pipeline


class Finding134PipelineAtomicityTests(TestCase):
    def test_approve_deployment_is_atomic(self):
        src = inspect.getsource(
            deployment_pipeline.ProductionDeploymentPipeline.approve_deployment,
        )
        self.assertIn("transaction.atomic", src)

    def test_reject_deployment_is_atomic(self):
        src = inspect.getsource(
            deployment_pipeline.ProductionDeploymentPipeline.reject_deployment,
        )
        self.assertIn("transaction.atomic", src)
