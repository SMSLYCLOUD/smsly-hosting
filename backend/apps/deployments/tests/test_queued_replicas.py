# pylint: disable=invalid-name
"""
Tests that ``_deploy_container`` honors the ``queued_min_replicas``
snapshot recorded at queue time, even if the autoscaler has since
mutated ``service.min_replicas``.

Behavior contract:
  * Snapshot at queue time = 1, current = 3 -> deploy uses 1.
  * Snapshot is None (legacy data) -> falls back to current.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments import tasks
from apps.deployments.models import Deployment, Service


class QueuedReplicasSnapshotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='replica-user', password='pwd')
        self.provider = CloudProvider.objects.create(
            name='replica-local',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='replica-svc',
            owner=self.user,
            provider=self.provider,
            min_replicas=1,
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.BUILDING,
            commit_hash='snap1',
            queued_min_replicas=1,
        )

    def test_uses_snapshot_when_present(self):
        """Autoscaler mutates min_replicas to 3 after queue, but the
        snapshot was 1. The deploy must use 1."""
        self.service.min_replicas = 2
        self.service.max_replicas = 5
        self.service.save(update_fields=['min_replicas', 'max_replicas'])

        compute = MagicMock()
        compute.deploy_container.return_value = MagicMock(resource_id='cid')
        with patch.object(tasks, 'ComputeService', return_value=compute), \
             patch.object(tasks, '_local_container_timeout_seconds', return_value=1), \
             patch.object(tasks, '_wait_for_local_container_healthy', return_value=True), \
             patch.object(tasks, '_regenerate_caddyfile', return_value=None), \
             patch.object(tasks, '_wait_for_local_route_ready', return_value=True), \
             patch.object(tasks, '_run_managed_image_post_deploy_hooks', return_value=None), \
             patch.object(tasks, '_post_deploy_monitor'):
            tasks._deploy_container(self.deployment, self.provider, 'img:1')

        kwargs = compute.deploy_container.call_args.kwargs
        self.assertEqual(kwargs['replicas'], 1)

    def test_falls_back_when_snapshot_missing(self):
        """A legacy Deployment row with no queued_min_replicas must
        fall back to the current service.min_replicas value."""
        legacy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.BUILDING,
            commit_hash='legacy1',
            queued_min_replicas=None,
        )
        self.service.min_replicas = 2
        self.service.max_replicas = 5
        self.service.save(update_fields=['min_replicas', 'max_replicas'])

        compute = MagicMock()
        compute.deploy_container.return_value = MagicMock(resource_id='cid2')
        with patch.object(tasks, 'ComputeService', return_value=compute), \
             patch.object(tasks, '_local_container_timeout_seconds', return_value=1), \
             patch.object(tasks, '_wait_for_local_container_healthy', return_value=True), \
             patch.object(tasks, '_regenerate_caddyfile', return_value=None), \
             patch.object(tasks, '_wait_for_local_route_ready', return_value=True), \
             patch.object(tasks, '_run_managed_image_post_deploy_hooks', return_value=None), \
             patch.object(tasks, '_post_deploy_monitor'):
            tasks._deploy_container(legacy, self.provider, 'img:2')

        kwargs = compute.deploy_container.call_args.kwargs
        self.assertEqual(kwargs['replicas'], 2)
