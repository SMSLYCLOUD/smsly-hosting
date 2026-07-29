# pylint: disable=invalid-name
"""
Regression tests for Issue 69 (5s heartbeat task = 12k
tasks/min at 1000 nodes).

The hot 5s heartbeat path is now a Redis pub/sub publish
plus a per-peer ``SETEX`` snapshot. Persistence to the DB is
moved to a 60s ``persist_heartbeats_task`` that drains the bus.
The tests in this file verify the bus contract: publish/get
and the persist task's drain behavior.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.deployments.models.election import HeartbeatLog
from apps.deployments.models.servers import ManagedServer

User = get_user_model()


class HeartbeatBusTests(SimpleTestCase):
    """Unit tests for the in-process pub/sub heartbeat bus."""

    def setUp(self):
        from apps.deployments.services import heartbeat_bus
        self._bus = heartbeat_bus

    def test_publish_returns_payload(self):
        fake_redis = _FakeRedis()
        with patch.object(self._bus, '_get_redis', return_value=fake_redis):
            payload = self._bus.publish_heartbeat(
                peer_id='10.100.0.1',
                wg_address='10.100.0.1',
                status='ALIVE',
                term=5,
            )
        self.assertIsNotNone(payload)
        self.assertEqual(payload['peer_id'], '10.100.0.1')
        self.assertEqual(payload['wg_address'], '10.100.0.1')
        self.assertEqual(payload['status'], 'ALIVE')
        self.assertEqual(payload['term'], 5)

    def test_publish_writes_snapshot_key_with_ttl(self):
        fake_redis = _FakeRedis()
        with patch.object(self._bus, '_get_redis', return_value=fake_redis):
            self._bus.publish_heartbeat(
                peer_id='10.100.0.1',
                wg_address='10.100.0.1',
                status='ALIVE',
            )
        keys = list(fake_redis.store.keys())
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].startswith('cluster_heartbeat:'))
        self.assertIn('10.100.0.1', keys[0])

    def test_publish_publishes_to_pubsub_channel(self):
        fake_redis = _FakeRedis()
        with patch.object(self._bus, '_get_redis', return_value=fake_redis):
            self._bus.publish_heartbeat(
                peer_id='10.100.0.2',
                wg_address='10.100.0.2',
                status='ALIVE',
            )
        self.assertIn('cluster_heartbeats', fake_redis.published)
        self.assertEqual(len(fake_redis.published['cluster_heartbeats']), 1)

    def test_publish_fails_open_on_redis_error(self):
        class _BrokenRedis:
            def publish(self, *a, **kw):
                import redis
                raise redis.RedisError("down")

            def setex(self, *a, **kw):
                import redis
                raise redis.RedisError("down")

        with patch.object(self._bus, '_get_redis', return_value=_BrokenRedis()):
            result = self._bus.publish_heartbeat(
                peer_id='x', wg_address='x', status='ALIVE',
            )
        self.assertIsNone(result)

    def test_get_latest_heartbeats_returns_snapshots(self):
        fake_redis = _FakeRedis()
        with patch.object(self._bus, '_get_redis', return_value=fake_redis):
            self._bus.publish_heartbeat(
                peer_id='10.100.0.1', wg_address='10.100.0.1',
                status='ALIVE', term=1,
            )
            self._bus.publish_heartbeat(
                peer_id='10.100.0.2', wg_address='10.100.0.2',
                status='ALIVE', term=1,
            )
            snapshots = self._bus.get_latest_heartbeats()
        self.assertEqual(len(snapshots), 2)
        peers = {s['peer_id'] for s in snapshots}
        self.assertEqual(peers, {'10.100.0.1', '10.100.0.2'})

    def test_get_latest_heartbeats_empty_when_no_redis(self):
        with patch.object(self._bus, '_get_redis', return_value=None):
            self.assertEqual(self._bus.get_latest_heartbeats(), [])


class HeartbeatBusPersistTests(TestCase):
    """Drains the bus into the DB — needs a real DB so the
    persist task can write a ``HeartbeatLog`` row."""

    def setUp(self):
        from apps.deployments.services import heartbeat_bus
        self._bus = heartbeat_bus
        self.user = User.objects.create_user(
            username='bus-user', password='123',
        )
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name='bus-peer',
            host='203.0.113.99',
            wg_address='10.100.0.99',
            api_url='https://203.0.113.99',
            gateway_secret='s',
        )

    def test_persist_heartbeats_task_drains_bus(self):
        fake_redis = _FakeRedis()
        with patch.object(self._bus, '_get_redis', return_value=fake_redis):
            self._bus.publish_heartbeat(
                peer_id='10.100.0.99',
                wg_address='10.100.0.99',
                status='ALIVE',
                term=7,
            )
            self._bus.persist_heartbeats_task()

        log = HeartbeatLog.objects.filter(target_server=self.server).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.term, 7)
        self.assertTrue(log.success)


class _FakeRedis:
    """Minimal in-memory stand-in for redis.Redis used by the
    heartbeat bus. Tracks ``publish`` calls and stores
    ``setex`` values with their TTL.

    Implements the surface the bus uses:
        - publish(channel, message) -> int
        - setex(key, ttl, value) -> bool
        - keys(pattern) -> list[str]
        - get(key) -> Optional[str]
    """

    def __init__(self):
        self.published = {}
        self.store = {}  # key -> (value, ttl)
        self._time = 1_000_000.0

    def publish(self, channel, message):
        self.published.setdefault(channel, []).append(message)
        return len(self.published[channel])

    def setex(self, key, ttl, value):
        self.store[key] = (value, ttl)

    def keys(self, pattern):
        if pattern.endswith('*'):
            prefix = pattern[:-1]
            return [k for k in self.store if k.startswith(prefix)]
        return [k for k in self.store if k == pattern]

    def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        return entry[0]
