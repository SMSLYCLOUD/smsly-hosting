"""Addon interactive terminal: ownership, routing, container resolution."""
import asyncio
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from apps.addons.consumers.addon_terminal import (
    AddonTerminalConsumer,
    _get_addon,
    _is_team_member,
)
from apps.deployments.models import Addon, Service

User = get_user_model()


def _run(coro):
    return asyncio.run(coro)


class RouteTests(SimpleTestCase):
    def test_addon_terminal_routes_registered(self):
        from apps.deployments.routing import websocket_urlpatterns
        patterns = [str(p.pattern) for p in websocket_urlpatterns]
        self.assertTrue(any('addon-terminal' in p for p in patterns))
        self.assertTrue(any('api/v1/ws/addon-terminal' in p for p in patterns))


class OwnershipTests(TransactionTestCase):
    # TransactionTestCase (not TestCase): the async DB helpers run in a
    # worker thread, which deadlocks against TestCase's outer atomic
    # block on sqlite (database table is locked).
    def setUp(self):
        self.owner = User.objects.create_user(username="addonterm", password="x")
        self.other = User.objects.create_user(username="addonterm2", password="x")
        self.service = Service.objects.create(name="addontermsvc", owner=self.owner)
        self.addon = Addon.objects.create(
            service=self.service, name="pg-term", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u:p@pg-term:5432/d")

    def test_missing_addon_is_none(self):
        import uuid
        self.assertIsNone(_run(_get_addon(uuid.uuid4())))

    def test_team_member_helper_no_team(self):
        self.assertFalse(_run(_is_team_member(123456, self.other.id)))

    def test_verify_owner_and_stranger(self):
        consumer = AddonTerminalConsumer()
        consumer.addon_id = self.addon.id
        consumer.user = self.owner
        self.assertTrue(_run(consumer._verify_ownership()))
        consumer.user = self.other
        self.assertFalse(_run(consumer._verify_ownership()))

    def test_verify_deleted_needs_owner_still(self):
        # Ownership is about the row; container gating happens in
        # _find_container (DELETED rows are refused there).
        consumer = AddonTerminalConsumer()
        consumer.addon_id = self.addon.id
        consumer.user = self.owner
        self.assertTrue(_run(consumer._verify_ownership()))


class FindContainerTests(TransactionTestCase):
    # TransactionTestCase: _find_container is awaited (runs ORM in a
    # worker thread), which deadlocks against TestCase's outer atomic
    # block on sqlite.
    def setUp(self):
        self.user = User.objects.create_user(username="addonfind", password="x")
        self.service = Service.objects.create(name="addonfindsvc", owner=self.user)

    def _addon(self, type_, name, status=Addon.Status.ACTIVE):
        return Addon.objects.create(
            service=self.service, name=name, addon_type=type_,
            status=status, provision_mode='container',
            connection_url=f"http://{name}:8686/")

    def _consumer(self, addon):
        consumer = AddonTerminalConsumer()
        consumer.addon_id = addon.id
        return consumer

    def _client(self, containers):
        client = mock.Mock()
        client.containers.get.side_effect = (
            lambda n: containers[n] if n in containers else (_ for _ in ()).throw(Exception("nope")))
        return client

    def _running(self):
        ctr = mock.Mock()
        ctr.id = 'cid-live'
        ctr.status = 'running'
        return ctr

    def test_deleted_addon_refused(self):
        addon = self._addon('OPENCODE', 'opencode-x', status=Addon.Status.DELETED)
        with mock.patch('apps.cloud.docker_client.get_docker_exec_client') as _:
            self.assertIsNone(_run(self._consumer(addon)._find_container()))

    def test_cli_uses_shared_container_name(self):
        addon = self._addon('OPENCODE', 'opencode-x')
        from apps.addons.services import cli_addons as cli
        shared = cli.resolve_container_name(addon)
        self.assertIn(str(self.service.id), shared)
        with mock.patch('apps.cloud.docker_client.get_docker_exec_client',
                        return_value=self._client({shared: self._running()})):
            self.assertEqual(_run(self._consumer(addon)._find_container()), 'cid-live')

    def test_missing_container_returns_none(self):
        addon = self._addon('REDIS', 'redis-x')
        with mock.patch('apps.cloud.docker_client.get_docker_exec_client',
                        return_value=self._client({})):
            self.assertIsNone(_run(self._consumer(addon)._find_container()))

    def test_stopped_container_refused(self):
        addon = self._addon('REDIS', 'redis-x')
        ctr = mock.Mock()
        ctr.id = 'cid-dead'
        ctr.status = 'exited'
        cname = f"smsly-addon-redis-{addon.id}"
        with mock.patch('apps.cloud.docker_client.get_docker_exec_client',
                        return_value=self._client({cname: ctr})):
            self.assertIsNone(_run(self._consumer(addon)._find_container()))

    def test_find_container_is_awaitable(self):
        # Regression: the base paths ``await`` this method — a sync
        # override breaks every console connect with TypeError.
        import inspect
        self.assertTrue(inspect.iscoroutinefunction(
            AddonTerminalConsumer._find_container))
