"""Tests for the Recent Scaling Decisions feed merge."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.autoscaler.models.replica import ServiceReplica
from apps.autoscaler.views.dashboard import _get_recent_decisions
from apps.deployments.models import Service

User = get_user_model()


class DecisionsFeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="decisions", password="p")
        self.service = Service.objects.create(
            name="feed-svc", owner=self.user, deploy_type="DOCKER",
            docker_image="registry:5000/feed-svc:latest",
        )
        now = timezone.now()
        self.t1, self.t2, self.t3 = (
            now - timedelta(hours=3),
            now - timedelta(hours=2),
            now - timedelta(hours=1),
        )
        self.d1, self.d2 = now - timedelta(minutes=30), now - timedelta(minutes=10)
        self.r1 = ServiceReplica.objects.create(
            service=self.service, node=None, container_name="feed-svc-replica-aaa",
            status="DESTROYED", spawn_reason="Manual spawn via API (horizontal)",
        )
        self.r2 = ServiceReplica.objects.create(
            service=self.service, node=None, container_name="feed-svc-replica-bbb",
            status="DESTROYED", spawn_reason="Manual spawn via API (horizontal)",
        )
        self.r3 = ServiceReplica.objects.create(
            service=self.service, node=None, container_name="feed-svc-replica-ccc",
            status="RUNNING", spawn_reason="Manual spawn via API (horizontal)",
        )
        ServiceReplica.objects.filter(id=self.r1.id).update(
            created_at=self.t1, destroyed_at=self.d1)
        ServiceReplica.objects.filter(id=self.r2.id).update(
            created_at=self.t2, destroyed_at=self.d2)
        ServiceReplica.objects.filter(id=self.r3.id).update(created_at=self.t3)

    def test_replica_events_appear_newest_first(self):
        decisions = _get_recent_decisions()
        self.assertEqual(len(decisions), 5)
        actions = [(d["action"], d["container"]) for d in decisions]
        self.assertEqual(actions, [
            ("scale_down", "feed-svc-replica-bbb"),
            ("scale_down", "feed-svc-replica-aaa"),
            ("scale_up", "feed-svc-replica-ccc"),
            ("scale_up", "feed-svc-replica-bbb"),
            ("scale_up", "feed-svc-replica-aaa"),
        ])

    def test_worker_counts_replayed_exactly(self):
        decisions = {d["container"] + d["action"]: d for d in _get_recent_decisions()}
        self.assertEqual(
            (decisions["feed-svc-replica-aaascale_up"]["current_workers"],
             decisions["feed-svc-replica-aaascale_up"]["target_workers"]),
            (0, 1),
        )
        self.assertEqual(
            (decisions["feed-svc-replica-bbbscale_up"]["current_workers"],
             decisions["feed-svc-replica-bbbscale_up"]["target_workers"]),
            (1, 2),
        )
        self.assertEqual(
            (decisions["feed-svc-replica-cccscale_up"]["current_workers"],
             decisions["feed-svc-replica-cccscale_up"]["target_workers"]),
            (2, 3),
        )
        self.assertEqual(
            (decisions["feed-svc-replica-aaascale_down"]["current_workers"],
             decisions["feed-svc-replica-aaascale_down"]["target_workers"]),
            (3, 2),
        )
        self.assertEqual(
            (decisions["feed-svc-replica-bbbscale_down"]["current_workers"],
             decisions["feed-svc-replica-bbbscale_down"]["target_workers"]),
            (2, 1),
        )

    def test_reasons_preserved(self):
        decisions = _get_recent_decisions()
        ups = [d for d in decisions if d["action"] == "scale_up"]
        self.assertTrue(all(
            "Manual spawn via API (horizontal)" in d["reason"] for d in ups
        ))
        downs = [d for d in decisions if d["action"] == "scale_down"]
        self.assertTrue(all("Replica removed" in d["reason"] for d in downs))
