"""Unit tests for replica parity helpers (no DB, no Docker)."""
from unittest import TestCase
from unittest.mock import MagicMock

from apps.deployments.services.replica_parity import (
    ReplicaParityError,
    assert_block_compatible,
    find_reference_container,
    live_service_blocks,
    merge_replica_env,
    parse_env_list,
    reference_config,
    traefik_service_block,
)

FULL_BLOCK = {
    "traefik.http.services.api.loadbalancer.server.port": "80",
    "traefik.http.services.api.loadbalancer.healthcheck.path": "/health",
    "traefik.http.services.api.loadbalancer.healthcheck.interval": "30s",
}
MINIMAL_BLOCK = {
    "traefik.http.services.api.loadbalancer.server.port": "80",
}


def _container(name, status="running", env=None, labels=None):
    c = MagicMock()
    c.name = name
    c.status = status
    c.attrs = {"Config": {"Env": env or [], "Labels": labels or {}}}
    return c


class ParseEnvTests(TestCase):
    def test_splits_on_first_equals_and_drops_hostname(self):
        env = parse_env_list([
            "PORT=80",
            "DATABASE_URL=postgres://u:p@h/db?x=1",
            "HOSTNAME=replica-1",
            "MALFORMED",
            "",
        ])
        self.assertEqual(env, {
            "PORT": "80",
            "DATABASE_URL": "postgres://u:p@h/db?x=1",
        })


class ServiceBlockTests(TestCase):
    def test_extracts_only_own_service_keys(self):
        labels = {
            **FULL_BLOCK,
            "traefik.http.services.other.loadbalancer.server.port": "80",
            "traefik.enable": "true",
        }
        self.assertEqual(traefik_service_block(labels, "api"), FULL_BLOCK)


class MergeEnvTests(TestCase):
    def test_live_base_with_db_overlay(self):
        merged = merge_replica_env(
            {"PORT": "80", "SECRET": "live-secret", "HOSTNAME": "x"},
            {"PORT": "8000", "NEW_KEY": "v"},
        )
        # HOSTNAME is dropped at parse time; here it passes through only
        # if explicitly present — DB rows win, live-only keys persist.
        self.assertEqual(merged["PORT"], "8000")
        self.assertEqual(merged["SECRET"], "live-secret")
        self.assertEqual(merged["NEW_KEY"], "v")


class AssertBlockTests(TestCase):
    def test_identical_passes(self):
        assert_block_compatible([dict(FULL_BLOCK)], dict(FULL_BLOCK), "api")

    def test_empty_references_pass(self):
        assert_block_compatible([{}, {}], dict(MINIMAL_BLOCK), "api")

    def test_partial_block_raises(self):
        with self.assertRaises(ReplicaParityError):
            assert_block_compatible([FULL_BLOCK], MINIMAL_BLOCK, "api")

    def test_missing_block_raises(self):
        with self.assertRaises(ReplicaParityError):
            assert_block_compatible([FULL_BLOCK], {}, "api")


class ReferenceTests(TestCase):
    def test_prefers_running_primary(self):
        primary = _container("api", status="running",
                             env=["PORT=80"],
                             labels={**FULL_BLOCK, "traefik.enable": "true"})
        sibling = _container("api-replica-1", status="running",
                             env=["PORT=80"], labels=dict(MINIMAL_BLOCK))
        client = MagicMock()
        client.containers.get.return_value = primary
        client.containers.list.return_value = [sibling]

        self.assertIs(find_reference_container(client, "api"), primary)
        env, block = reference_config(client, "api")
        self.assertEqual(env, {"PORT": "80"})
        self.assertEqual(block, FULL_BLOCK)
        self.assertEqual(
            live_service_blocks(client, "api"), [FULL_BLOCK, MINIMAL_BLOCK])

    def test_no_containers_returns_empty(self):
        client = MagicMock()
        client.containers.get.side_effect = Exception("not found")
        client.containers.list.return_value = []
        self.assertIsNone(find_reference_container(client, "api"))
        self.assertEqual(reference_config(client, "api"), ({}, {}))
