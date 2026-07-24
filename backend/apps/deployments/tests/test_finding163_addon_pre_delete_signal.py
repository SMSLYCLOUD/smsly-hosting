# pylint: disable=invalid-name
"""Regression tests for Finding #163 (Addon pre_delete cleanup).

Before the fix, deleting an ``Addon`` row only removed the
``Addon`` itself; the underlying Docker volume and Coolify record
were left behind because the cleanup Celery task was only ever
invoked from the API delete path, not the ORM ``.delete()`` path.
The fix wires a ``pre_delete`` signal on ``Addon`` that dispatches
``deprovision_addon_task`` and removes the addon Docker volume via
the docker SDK so any path that deletes the row leaves the
infrastructure in a clean state.

These tests verify:
  * Deleting an ``Addon`` row calls ``deprovision_addon_task`` (or
    the docker-volume fallback if Celery is unavailable).
  * The signal also attempts to remove the addon Docker volume
    through the docker SDK.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class Finding163AddonPreDeleteSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fix163-user', password='x',
        )
        self.service = Service.objects.create(
            name='fix163-svc', owner=self.user,
        )
        self.addon = Addon.objects.create(
            service=self.service,
            name='fix163-addon',
            addon_type=Addon.Type.POSTGRES,
        )

    def test_pre_delete_dispatches_deprovision_task(self):
        """Deleting an Addon row triggers ``deprovision_addon_task``."""
        addon_id_str = str(self.addon.id)
        with patch(
            "apps.deployments.tasks.deprovision_addon_task.delay",
        ) as mock_delay:
            self.addon.delete()
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.args[0], addon_id_str)

    def test_pre_delete_attempts_docker_volume_removal(self):
        """The signal also asks the docker SDK to drop the volume."""
        fake_volume = MagicMock()
        fake_client = MagicMock()
        fake_client.volumes.get.return_value = fake_volume
        with patch(
            "apps.deployments.tasks.deprovision_addon_task.delay",
        ), patch(
            "docker.from_env",
            return_value=fake_client,
        ):
            self.addon.delete()
        fake_client.volumes.get.assert_called_once()
        fake_volume.remove.assert_called_once_with(force=True)

    def test_pre_delete_swallows_docker_errors(self):
        """docker SDK errors must not block the ORM delete."""
        with patch(
            "apps.deployments.tasks.deprovision_addon_task.delay",
        ), patch(
            "docker.from_env",
            side_effect=RuntimeError("docker not reachable"),
        ):
            self.addon.delete()
        self.assertFalse(Addon.objects.filter(pk=self.addon.id).exists())
