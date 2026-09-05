from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.cloud.models.backup import ServiceSnapshot
from apps.deployments.models import Service
from apps.deployments.models.ecosystem import EcosystemPlan

User = get_user_model()


def _restore_url(plan_id):
    return reverse("ecosystem-plan-restore-snapshots", args=[str(plan_id)])


class EcosystemPlanRestoreSnapshotsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="plan-restore", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(name="restore-svc", owner=self.user)
        self.snapshot = ServiceSnapshot.objects.create(
            service=self.service,
            trigger=ServiceSnapshot.Trigger.PRE_DEPLOY,
            label="Pre-ecosystem-deploy: restore-svc",
            config_data={"branch": "release-1.2", "env_vars": {}},
        )
        self.plan = EcosystemPlan.objects.create(
            user=self.user,
            status=EcosystemPlan.Status.FAILED,
            services_created=[{
                "name": self.service.name,
                "service_id": str(self.service.id),
                "deployment_id": "00000000-0000-0000-0000-000000000000",
                "pre_deploy_snapshot_id": str(self.snapshot.id),
                "status": "queued",
            }],
        )

    def test_restore_applies_snapshot_config(self):
        resp = self.client.post(
            _restore_url(self.plan.id),
            {"confirm": True, "redeploy": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["restored"]), 1)
        self.assertEqual(resp.data["restored"][0]["service_id"], str(self.service.id))
        self.assertEqual(resp.data["errors"], [])
        self.service.refresh_from_db()
        self.assertEqual(self.service.branch, "release-1.2")

    def test_restore_requires_confirm(self):
        resp = self.client.post(
            _restore_url(self.plan.id), {"redeploy": False}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_restore_refused_while_deploying(self):
        self.plan.status = EcosystemPlan.Status.DEPLOYING
        self.plan.save(update_fields=["status"])
        resp = self.client.post(
            _restore_url(self.plan.id),
            {"confirm": True, "redeploy": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_entry_without_snapshot_is_skipped(self):
        self.plan.services_created = [{
            "name": "fresh-svc",
            "service_id": "00000000-0000-0000-0000-000000000001",
            "pre_deploy_snapshot_id": None,
            "status": "queued",
        }]
        self.plan.save(update_fields=["services_created"])
        resp = self.client.post(
            _restore_url(self.plan.id),
            {"confirm": True, "redeploy": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["restored"], [])
        self.assertEqual(len(resp.data["skipped"]), 1)

    def test_other_users_plan_is_not_found(self):
        other = User.objects.create_user(username="plan-restore-other", password="pw")
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        resp = other_client.post(
            _restore_url(self.plan.id),
            {"confirm": True, "redeploy": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
