# pylint: disable=invalid-name
"""Tests for WebSocket consumers.

Exercises connection handshake, authentication, message dispatch,
and disconnect cleanup for BuildLogConsumer, RuntimeLogConsumer,
ServiceStatusConsumer, and TerminalConsumer.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token

from apps.deployments.consumers.build_log import BuildLogConsumer
from apps.deployments.consumers.runtime_log import RuntimeLogConsumer
from apps.deployments.consumers.service_status import ServiceStatusConsumer
from apps.deployments.consumers.terminal import TerminalConsumer

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "consumer-test-caches",
    }
}

CHANNEL_LAYERS_TEST = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


def _make_communicator(consumer_cls, path, *, user=None, query_string='',
                        subprotocols=None):
    """Build a WebsocketCommunicator with auth injected into scope."""
    scope = {
        'type': 'websocket',
        'path': path,
        'query_string': query_string,
        'url_route': {'args': (), 'kwargs': {}},
        'subprotocols': subprotocols or [],
        'headers': [],
    }
    if user is not None:
        scope['user'] = user
    comm = WebsocketCommunicator(consumer_cls.as_asgi(), path)
    comm.scope.update(scope)
    return comm


# ---------------------------------------------------------------------------
# BuildLogConsumer
# ---------------------------------------------------------------------------
@override_settings(CACHES=TEST_CACHES, CHANNEL_LAYERS=CHANNEL_LAYERS_TEST)
class BuildLogConsumerTests(TestCase):
    """Tests for BuildLogConsumer connection and message flow."""

    def setUp(self):
        self.user = User.objects.create_user(username='bl_user', password='x')
        self.token = Token.objects.create(user=self.user)
        self.deployment_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

    async def test_connect_rejects_no_user(self):
        """Anonymous scope (no user) is rejected with close(4001)."""
        comm = WebsocketCommunicator(
            BuildLogConsumer.as_asgi(),
            f'/ws/build-logs/{self.deployment_id}/',
        )
        comm.scope['url_route'] = {
            'args': (),
            'kwargs': {'deployment_id': self.deployment_id},
        }
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_rejects_invalid_token(self):
        """Unauthenticated user is rejected with close(4001)."""
        comm = WebsocketCommunicator(
            BuildLogConsumer.as_asgi(),
            f'/ws/build-logs/{self.deployment_id}/',
        )
        comm.scope['url_route'] = {
            'args': (),
            'kwargs': {'deployment_id': self.deployment_id},
        }
        comm.scope['user'] = MagicMock(is_authenticated=False)
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_accepts_valid_owner(self):
        """Authenticated owner gets accepted and receives initial_state."""
        with patch(
            'apps.deployments.consumers.build_log.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ), patch(
            'apps.deployments.consumers.build_log.BuildLogConsumer._get_current_state',
            new_callable=AsyncMock, return_value={
                'build_logs': '',
                'status': 'BUILDING',
                'started_at': None,
                'finished_at': None,
                'duration_seconds': None,
                'commit_hash': '',
                'commit_message': '',
            },
        ):
            comm = WebsocketCommunicator(
                BuildLogConsumer.as_asgi(),
                f'/ws/build-logs/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)

            response = await comm.receive_json_from(timeout=2)
            self.assertEqual(response['type'], 'initial_state')
            self.assertIn('status', response)
            self.assertIn('build_logs', response)
            await comm.disconnect()

    async def test_build_log_event_dispatched_to_client(self):
        """group_send of build_log is forwarded to the client."""
        with patch(
            'apps.deployments.consumers.build_log.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ), patch(
            'apps.deployments.consumers.build_log.BuildLogConsumer._get_current_state',
            new_callable=AsyncMock, return_value={
                'build_logs': '', 'status': 'QUEUED',
                'started_at': None, 'finished_at': None,
                'duration_seconds': None,
                'commit_hash': '', 'commit_message': '',
            },
        ):
            comm = WebsocketCommunicator(
                BuildLogConsumer.as_asgi(),
                f'/ws/build-logs/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from(timeout=2)  # initial_state

            # Simulate Celery pushing a log via channel layer
            group_name = f"build_logs_{self.deployment_id}"
            await comm.instance.channel_layer.group_send(
                group_name, {
                    'type': 'build_log',
                    'log': 'Compiling assets...\n',
                    'status': 'BUILDING',
                    'timestamp': '2026-07-26T10:00:00Z',
                }
            )

            msg = await comm.receive_json_from(timeout=2)
            self.assertEqual(msg['type'], 'build_log')
            self.assertEqual(msg['log'], 'Compiling assets...\n')
            self.assertEqual(msg['status'], 'BUILDING')
            self.assertIn('timestamp', msg)
            await comm.disconnect()

    async def test_disconnect_removes_from_group(self):
        """After disconnect, channel is removed from the log group."""
        with patch(
            'apps.deployments.consumers.build_log.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ), patch(
            'apps.deployments.consumers.build_log.BuildLogConsumer._get_current_state',
            new_callable=AsyncMock, return_value={
                'build_logs': '', 'status': 'BUILDING',
                'started_at': None, 'finished_at': None,
                'duration_seconds': None,
                'commit_hash': '', 'commit_message': '',
            },
        ):
            comm = WebsocketCommunicator(
                BuildLogConsumer.as_asgi(),
                f'/ws/build-logs/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from(timeout=2)

            group_name = f"build_logs_{self.deployment_id}"
            channel_name = comm.instance.channel_name
            channel_layer = comm.instance.channel_layer
            self.assertIn(channel_name,
                          channel_layer.groups.get(group_name, set()))

            await comm.disconnect()
            # After disconnect the channel should be gone from the group
            self.assertNotIn(
                channel_name,
                channel_layer.groups.get(group_name, set()),
            )


# ---------------------------------------------------------------------------
# RuntimeLogConsumer
# ---------------------------------------------------------------------------
@override_settings(CACHES=TEST_CACHES, CHANNEL_LAYERS=CHANNEL_LAYERS_TEST)
class RuntimeLogConsumerTests(TestCase):
    """Tests for RuntimeLogConsumer connection and log dispatch."""

    def setUp(self):
        self.user = User.objects.create_user(username='rl_user', password='x')
        self.token = Token.objects.create(user=self.user)
        self.deployment_id = 'aaaaaaaa-bbbb-cccc-dddd-ffffffffffff'

    async def test_connect_rejects_anonymous(self):
        """Anonymous scope is rejected with close(4002)."""
        comm = WebsocketCommunicator(
            RuntimeLogConsumer.as_asgi(),
            f'/ws/runtime-logs/{self.deployment_id}/',
        )
        comm.scope['url_route'] = {
            'args': (),
            'kwargs': {'deployment_id': self.deployment_id},
        }
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_accepts_valid_owner(self):
        """Authenticated owner gets accepted and receives initial_state."""
        with patch(
            'apps.deployments.consumers.runtime_log.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ), patch(
            'apps.deployments.consumers.runtime_log.RuntimeLogConsumer._get_initial_state',
            new_callable=AsyncMock, return_value={
                'logs': '',
                'status': 'ACTIVE',
                'container_id': 'abc123',
                'container_status': 'stopped',
                'source': 'build_logs',
            },
        ):
            comm = WebsocketCommunicator(
                RuntimeLogConsumer.as_asgi(),
                f'/ws/runtime-logs/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)

            msg = await comm.receive_json_from(timeout=2)
            self.assertEqual(msg['type'], 'initial_state')
            self.assertEqual(msg['container_status'], 'stopped')
            await comm.disconnect()

    async def test_log_event_dispatched_to_client(self):
        """log_event group message is forwarded to the client."""
        with patch(
            'apps.deployments.consumers.runtime_log.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ), patch(
            'apps.deployments.consumers.runtime_log.RuntimeLogConsumer._get_initial_state',
            new_callable=AsyncMock, return_value={
                'logs': '', 'status': 'ACTIVE',
                'container_id': 'abc123',
                'container_status': 'stopped',
                'source': 'build_logs',
            },
        ):
            comm = WebsocketCommunicator(
                RuntimeLogConsumer.as_asgi(),
                f'/ws/runtime-logs/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from(timeout=2)  # initial_state

            group_name = f"runtime_logs_{self.deployment_id}"
            await comm.instance.channel_layer.group_send(
                group_name, {
                    'type': 'log_event',
                    'log': 'Listening on port 3000\n',
                    'timestamp': '2026-07-26T11:00:00Z',
                }
            )

            msg = await comm.receive_json_from(timeout=2)
            self.assertEqual(msg['type'], 'log')
            self.assertEqual(msg['log'], 'Listening on port 3000\n')
            await comm.disconnect()

    async def test_disconnect_cleans_up_stream(self):
        """Disconnect cancels stream task and removes group membership."""
        with patch(
            'apps.deployments.consumers.runtime_log.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ), patch(
            'apps.deployments.consumers.runtime_log.RuntimeLogConsumer._get_initial_state',
            new_callable=AsyncMock, return_value={
                'logs': '', 'status': 'ACTIVE',
                'container_id': 'abc123',
                'container_status': 'stopped',
                'source': 'build_logs',
            },
        ):
            comm = WebsocketCommunicator(
                RuntimeLogConsumer.as_asgi(),
                f'/ws/runtime-logs/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from(timeout=2)

            group_name = f"runtime_logs_{self.deployment_id}"
            channel_name = comm.instance.channel_name
            self.assertIn(channel_name,
                          comm.instance.channel_layer.groups.get(
                              group_name, set()))

            await comm.disconnect()
            self.assertNotIn(
                channel_name,
                comm.instance.channel_layer.groups.get(group_name, set()),
            )


# ---------------------------------------------------------------------------
# ServiceStatusConsumer
# ---------------------------------------------------------------------------
@override_settings(CACHES=TEST_CACHES, CHANNEL_LAYERS=CHANNEL_LAYERS_TEST)
class ServiceStatusConsumerTests(TestCase):
    """Tests for ServiceStatusConsumer connection and dispatch."""

    def setUp(self):
        self.user = User.objects.create_user(username='ss_user', password='x')
        self.token = Token.objects.create(user=self.user)

    async def test_connect_rejects_unauthenticated(self):
        """Anonymous scope is rejected with close(4001)."""
        comm = WebsocketCommunicator(
            ServiceStatusConsumer.as_asgi(),
            '/ws/service-status/',
        )
        comm.scope['url_route'] = {'args': (), 'kwargs': {}}
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_accepts_and_sends_initial_services(self):
        """Authenticated user gets accepted and receives initial service list."""
        with patch(
            'apps.deployments.consumers.service_status'
            '.ServiceStatusConsumer._get_user_services',
            new_callable=AsyncMock, return_value=[
                {
                    'id': 'svc-uuid-1',
                    'name': 'my-app',
                    'status': 'ACTIVE',
                    'deployment_status': 'ACTIVE',
                    'updated_at': '2026-07-26T10:00:00Z',
                },
            ],
        ):
            comm = WebsocketCommunicator(
                ServiceStatusConsumer.as_asgi(),
                '/ws/service-status/',
            )
            comm.scope['url_route'] = {'args': (), 'kwargs': {}}
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)

            msg = await comm.receive_json_from(timeout=2)
            self.assertEqual(msg['type'], 'service_status_update')
            self.assertEqual(msg['service_id'], 'svc-uuid-1')
            self.assertEqual(msg['service_name'], 'my-app')
            self.assertEqual(msg['status'], 'ACTIVE')
            await comm.disconnect()

    async def test_service_status_update_dispatched(self):
        """channel_layer group_send is forwarded to client."""
        with patch(
            'apps.deployments.consumers.service_status'
            '.ServiceStatusConsumer._get_user_services',
            new_callable=AsyncMock, return_value=[],
        ):
            comm = WebsocketCommunicator(
                ServiceStatusConsumer.as_asgi(),
                '/ws/service-status/',
            )
            comm.scope['url_route'] = {'args': (), 'kwargs': {}}
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            # Drain the initial services (empty list sends nothing,
            # but the group is joined)

            group_name = f"user_services_{self.user.id}"
            await comm.instance.channel_layer.group_send(
                group_name, {
                    'type': 'service_status_update',
                    'service_id': 'svc-uuid-2',
                    'service_name': 'api-gw',
                    'status': 'FAILED',
                    'deployment_status': 'BUILD_FAILED',
                    'updated_at': '2026-07-26T12:00:00Z',
                }
            )

            msg = await comm.receive_json_from(timeout=2)
            self.assertEqual(msg['type'], 'service_status_update')
            self.assertEqual(msg['service_id'], 'svc-uuid-2')
            self.assertEqual(msg['status'], 'FAILED')
            self.assertEqual(msg['deployment_status'], 'BUILD_FAILED')
            await comm.disconnect()

    async def test_disconnect_removes_from_user_group(self):
        """After disconnect the channel leaves the user group."""
        with patch(
            'apps.deployments.consumers.service_status'
            '.ServiceStatusConsumer._get_user_services',
            new_callable=AsyncMock, return_value=[],
        ):
            comm = WebsocketCommunicator(
                ServiceStatusConsumer.as_asgi(),
                '/ws/service-status/',
            )
            comm.scope['url_route'] = {'args': (), 'kwargs': {}}
            comm.scope['user'] = self.user
            connected, _ = await comm.connect()
            self.assertTrue(connected)

            group_name = f"user_services_{self.user.id}"
            channel_name = comm.instance.channel_name
            self.assertIn(channel_name,
                          comm.instance.channel_layer.groups.get(
                              group_name, set()))

            await comm.disconnect()
            self.assertNotIn(
                channel_name,
                comm.instance.channel_layer.groups.get(group_name, set()),
            )


# ---------------------------------------------------------------------------
# TerminalConsumer
# ---------------------------------------------------------------------------
@override_settings(CACHES=TEST_CACHES, CHANNEL_LAYERS=CHANNEL_LAYERS_TEST)
class TerminalConsumerTests(TestCase):
    """Tests for TerminalConsumer token-based auth handshake."""

    def setUp(self):
        self.user = User.objects.create_user(username='term_user', password='x')
        self.token = Token.objects.create(user=self.user)
        self.deployment_id = 'aaaaaaaa-bbbb-cccc-dddd-111111111111'

    async def test_connect_rejects_no_token(self):
        """No subprotocol token offered → close(4001)."""
        comm = WebsocketCommunicator(
            TerminalConsumer.as_asgi(),
            f'/ws/terminal/{self.deployment_id}/',
        )
        comm.scope['url_route'] = {
            'args': (),
            'kwargs': {'deployment_id': self.deployment_id},
        }
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_rejects_invalid_token(self):
        """Bad token in subprotocol → close(4002)."""
        comm = WebsocketCommunicator(
            TerminalConsumer.as_asgi(),
            f'/ws/terminal/{self.deployment_id}/',
        )
        comm.scope['url_route'] = {
            'args': (),
            'kwargs': {'deployment_id': self.deployment_id},
        }
        comm.scope['subprotocols'] = ['token', 'garbage-token']
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_accepts_valid_token_pair(self):
        """Valid ['token', '<key>'] subprotocols are accepted."""
        with patch(
            'apps.deployments.consumers.terminal.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=True,
        ):
            comm = WebsocketCommunicator(
                TerminalConsumer.as_asgi(),
                f'/ws/terminal/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['subprotocols'] = ['token', self.token.key]
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.disconnect()

    async def test_connect_rejects_ownership(self):
        """Valid token but wrong ownership → close(4003)."""
        with patch(
            'apps.deployments.consumers.terminal.verify_deployment_ownership',
            new_callable=AsyncMock, return_value=False,
        ):
            comm = WebsocketCommunicator(
                TerminalConsumer.as_asgi(),
                f'/ws/terminal/{self.deployment_id}/',
            )
            comm.scope['url_route'] = {
                'args': (),
                'kwargs': {'deployment_id': self.deployment_id},
            }
            comm.scope['subprotocols'] = ['token', self.token.key]
            connected, _ = await comm.connect()
            self.assertFalse(connected)
