# pylint: disable=invalid-name
"""Regression tests for Finding #149 (send_approval_notification).

Before the fix, ``send_approval_notification`` was a no-op that only
logged. The fix dispatches the approval-state change via
``apps.notifications.tasks.dispatch_notification`` and falls back to
``django.core.mail.send_mail`` if the dispatcher is unavailable.
Both code paths must swallow backend errors so the approve/reject
flow is never blocked by an unconfigured email backend.

These tests verify:
  * The function calls ``dispatch_notification.delay`` with the
    requester's user id and email channel.
  * It falls back to ``send_mail`` when the dispatcher is unavailable.
  * It is a no-op (no exception) when the requester has no email.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.safedeploy import DeploymentApproval
from apps.deployments.views.safedeploy import send_approval_notification

User = get_user_model()


class Finding149SendApprovalNotificationTests(TestCase):
    def setUp(self):
        self.requester = User.objects.create_user(
            username='fix149-requester', password='x',
            email='requester@example.com',
        )
        self.service = Service.objects.create(
            name='fix149-svc', owner=self.requester,
        )
        self.approval = DeploymentApproval.objects.create(
            service=self.service,
            requested_by=self.requester,
            status=DeploymentApproval.Status.APPROVED,
        )

    def test_no_email_is_a_no_op(self):
        """A requester without an email address does not raise and does
        not attempt to send anything."""
        self.requester.email = ''
        self.requester.save(update_fields=['email'])

        with patch(
            'apps.notifications.tasks.dispatch_notification.delay',
        ) as mock_dispatch, patch(
            'django.core.mail.send_mail',
        ) as mock_send_mail:
            send_approval_notification(self.approval, self.service.id)

        mock_dispatch.assert_not_called()
        mock_send_mail.assert_not_called()

    def test_uses_dispatch_notification_when_available(self):
        """The primary path queues a ``dispatch_notification`` task."""
        with patch(
            'apps.notifications.tasks.dispatch_notification.delay',
        ) as mock_dispatch:
            send_approval_notification(self.approval, self.service.id)

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args.kwargs
        self.assertEqual(kwargs['user_id'], self.requester.id)
        self.assertEqual(kwargs['channels'], ['email'])
        self.assertEqual(kwargs['metadata']['approval_id'], str(self.approval.id))

    def test_falls_back_to_send_mail_when_dispatcher_unavailable(self):
        """If the dispatcher raises, the function falls back to send_mail."""
        with patch(
            'apps.notifications.tasks.dispatch_notification.delay',
            side_effect=RuntimeError('celery down'),
        ) as mock_dispatch, patch(
            'django.core.mail.send_mail',
            return_value=1,
        ) as mock_send_mail:
            send_approval_notification(self.approval, self.service.id)

        mock_dispatch.assert_called_once()
        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args
        self.assertEqual(call_args.kwargs.get('recipient_list'), [self.requester.email])
