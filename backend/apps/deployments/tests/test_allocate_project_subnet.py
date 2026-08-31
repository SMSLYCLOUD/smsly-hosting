"""Unit tests for the project subnet allocator (no DB / no Docker needed
for the pure-selection logic — Docker reads are patched)."""
from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.services.network_scope import allocate_project_subnet


class _FakeConfig:
    def __init__(self, subnet):
        self.default_internal_subnet = subnet


class _FakePlatformConfig:
    def __init__(self, subnet):
        self._cfg = _FakeConfig(subnet)

    def load(self):
        return self._cfg


class AllocateProjectSubnetTests(SimpleTestCase):
    def _patch(self, existing, default=''):
        pc = _FakePlatformConfig(default)
        return mock.patch(
            'apps.deployments.services.network_scope._existing_docker_subnets',
            return_value=set(existing),
        ), mock.patch(
            'apps.deployments.services.network_scope.PlatformConfig_load',
            pc.load,
            create=True,
        )

    def test_explicit_override_wins(self):
        with mock.patch(
            'apps.deployments.services.network_scope._existing_docker_subnets',
            return_value={'10.99.0.0/24'},
        ):
            # The operator asked for this exact subnet; even if it overlaps
            # (10.99.0.0/24 is in existing) we return it so networks.create
            # fails loudly instead of silently allocating something else.
            self.assertEqual(
                allocate_project_subnet(requested='10.99.0.0/24'),
                '10.99.0.0/24',
            )

    def test_default_used_when_free(self):
        with mock.patch(
            'apps.deployments.services.network_scope._existing_docker_subnets',
            return_value={'172.18.0.0/16'},
        ), mock.patch(
            'apps.deployments.models.core.PlatformConfig.load',
            return_value=_FakeConfig('172.30.224.0/24'),
        ):
            self.assertEqual(
                allocate_project_subnet(),
                '172.30.224.0/24',
            )

    def test_default_taken_finds_next_free_slash24(self):
        # The overlap bug: first project already holds the default.
        with mock.patch(
            'apps.deployments.services.network_scope._existing_docker_subnets',
            return_value={'172.18.0.0/16', '172.30.224.0/24'},
        ), mock.patch(
            'apps.deployments.models.core.PlatformConfig.load',
            return_value=_FakeConfig('172.30.224.0/24'),
        ):
            result = allocate_project_subnet()
        self.assertEqual(result, '172.30.1.0/24')

    def test_scans_past_allocated_slash24s(self):
        existing = {
            '172.18.0.0/16',          # default bridge
            '172.30.224.0/24',        # platform default — taken by project 1
            '172.30.1.0/24',          # allocator output — taken by project 2
            '172.30.2.0/24',          # allocator output — taken by project 3
        }
        with mock.patch(
            'apps.deployments.services.network_scope._existing_docker_subnets',
            return_value=set(existing),
        ), mock.patch(
            'apps.deployments.models.core.PlatformConfig.load',
            return_value=_FakeConfig('172.30.224.0/24'),
        ):
            result = allocate_project_subnet()
        self.assertEqual(result, '172.30.3.0/24')

    def test_no_default_scans_pool(self):
        with mock.patch(
            'apps.deployments.services.network_scope._existing_docker_subnets',
            return_value={'172.30.224.0/24'},
        ), mock.patch(
            'apps.deployments.models.core.PlatformConfig.load',
            return_value=_FakeConfig(''),
        ):
            result = allocate_project_subnet()
        self.assertEqual(result, '172.30.1.0/24')
