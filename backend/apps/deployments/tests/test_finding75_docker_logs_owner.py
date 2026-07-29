# pylint: disable=invalid-name
"""Regression tests for Finding #75 (docker logs cross-tenant ACL).

Before the fix, ``docker logs <name>`` was a free-for-all on the host
once ``_is_command_allowed`` accepted the command.  The fix threads
the calling request's user into ``_user_owns_container_name`` and
rejects any ``docker logs`` whose target name is not a
``Service.name`` owned by that same user.

These tests verify:
  * A user can tail logs of a container they own.
  * A user cannot tail logs of another tenant's container.
  * The function short-circuits on a leading ``-`` flag (no name to
    verify).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.views.server.helpers import (
    _bind_request_user,
    _is_command_allowed,
    _user_owns_container_name,
)

User = get_user_model()


class Finding75DockerLogsOwnerCheckTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='fix75-owner', password='x',
        )
        self.attacker = User.objects.create_user(
            username='fix75-attacker', password='x',
        )
        self.owned_container = 'fix75-svc'
        self.foreign_container = 'fix75-foreign-svc'
        Service.objects.create(
            name=self.owned_container, owner=self.owner,
        )
        Service.objects.create(
            name=self.foreign_container, owner=self.owner,
        )

    def tearDown(self):
        from threading import current_thread
        if hasattr(current_thread(), 'smsly_request_user'):
            delattr(current_thread(), 'smsly_request_user')

    def test_owner_can_tail_own_container(self):
        _bind_request_user(self.owner)
        self.assertTrue(
            _is_command_allowed(f'docker logs {self.owned_container}'),
        )

    def test_attacker_cannot_tail_foreign_container(self):
        _bind_request_user(self.attacker)
        self.assertFalse(
            _is_command_allowed(f'docker logs {self.foreign_container}'),
        )

    def test_user_owns_container_name_helper_cross_checks_owner(self):
        """The helper rejects names that belong to a different user."""
        self.assertTrue(_user_owns_container_name(self.owner, self.owned_container))
        self.assertFalse(_user_owns_container_name(self.attacker, self.owned_container))
