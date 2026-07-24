# pylint: disable=invalid-name
"""Tests for the ``CronJob.schedule`` pre_save validator (Issue 136).

Without a model-level validator, a user can set ``* * * * *`` to fire
every minute, DOS'ing the platform.  The pre_save signal enforces a
5-minute minimum cadence and a basic regex shape.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.cron import CronJob

User = get_user_model()


class CronJobSchedulePreSaveSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cron-user", password="x",
        )
        self.provider = CloudProvider.objects.create(
            name="cron-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="cron-svc",
            owner=self.user,
            provider=self.provider,
        )

    def test_rejects_every_minute(self):
        cj = CronJob(
            service=self.service, name="bad1", schedule="* * * * *",
            command="echo",
        )
        with self.assertRaises(ValidationError) as ctx:
            cj.save()
        self.assertIn("schedule", ctx.exception.message_dict)

    def test_rejects_every_two_minutes(self):
        cj = CronJob(
            service=self.service, name="bad2",
            schedule="*/2 * * * *", command="echo",
        )
        with self.assertRaises(ValidationError):
            cj.save()

    def test_rejects_wildcard_with_step_1(self):
        cj = CronJob(
            service=self.service, name="bad3",
            schedule="*/1 * * * *", command="echo",
        )
        with self.assertRaises(ValidationError):
            cj.save()

    def test_rejects_dense_comma_list(self):
        cj = CronJob(
            service=self.service, name="bad4",
            schedule="0,1,2,3,4 * * * *", command="echo",
        )
        with self.assertRaises(ValidationError):
            cj.save()

    def test_rejects_non_numeric_characters(self):
        cj = CronJob(
            service=self.service, name="bad5",
            schedule="*/5 * * * $(reboot)", command="echo",
        )
        with self.assertRaises(ValidationError):
            cj.save()

    def test_rejects_wrong_field_count(self):
        cj = CronJob(
            service=self.service, name="bad6",
            schedule="* * *", command="echo",
        )
        with self.assertRaises(ValidationError) as ctx:
            cj.save()
        self.assertIn("5 fields", str(ctx.exception.message_dict["schedule"]))

    def test_accepts_every_five_minutes(self):
        cj = CronJob(
            service=self.service, name="ok1",
            schedule="*/5 * * * *", command="echo",
        )
        cj.save()
        cj.refresh_from_db()
        self.assertEqual(cj.schedule, "*/5 * * * *")

    def test_accepts_every_ten_minutes(self):
        cj = CronJob(
            service=self.service, name="ok2",
            schedule="*/10 * * * *", command="echo",
        )
        cj.save()

    def test_accepts_daily_at_specific_time(self):
        cj = CronJob(
            service=self.service, name="ok3",
            schedule="0 3 * * *", command="echo",
        )
        cj.save()

    def test_rejects_unsafe_char_in_field(self):
        cj = CronJob(
            service=self.service, name="bad7",
            schedule="0 3 * * ;reboot", command="echo",
        )
        with self.assertRaises(ValidationError):
            cj.save()
