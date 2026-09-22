"""Unit tests for promotion readiness probes (readiness + smoke seams)."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import docker

from apps.deployments.services.safedeploy import promotion_guard as guard


def _service(**kwargs):
    base = {'internal_port': 8080, 'readiness_path': '', 'smoke_command': ''}
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestGreenBaseUrl(TestCase):
    def test_builds_url_from_container_name(self):
        fake = MagicMock()
        fake.name = 'svc-green-abc123'
        with patch('docker.from_env') as from_env:
            from_env.return_value.containers.get.return_value = fake
            self.assertEqual(
                guard._green_base_url('abc123', _service()),
                'http://svc-green-abc123:8080',
            )

    def test_none_when_no_name(self):
        fake = MagicMock()
        fake.name = ''
        with patch('docker.from_env') as from_env:
            from_env.return_value.containers.get.return_value = fake
            self.assertIsNone(guard._green_base_url('abc', _service()))

    def test_none_on_inspect_failure(self):
        with patch('docker.from_env', side_effect=RuntimeError('down')):
            self.assertIsNone(guard._green_base_url('abc', _service()))


class TestProbeGreenReadiness(TestCase):
    def test_unconfigured_passes(self):
        ok, detail = guard._probe_green_readiness('http://x:8080', _service())
        self.assertTrue(ok)
        self.assertIn('not configured', detail)

    def test_failure_propagates(self):
        svc = _service(readiness_path='/ready')
        with patch(
            'apps.deployments.services.safedeploy.health_checks.perform_health_check',
            return_value=(False, MagicMock()),
        ):
            ok, detail = guard._probe_green_readiness('http://x:8080', svc)
        self.assertFalse(ok)
        self.assertIn('/ready', detail)

    def test_success(self):
        svc = _service(readiness_path='ready')
        with patch(
            'apps.deployments.services.safedeploy.health_checks.perform_health_check',
            return_value=(True, MagicMock()),
        ) as phc:
            ok, _ = guard._probe_green_readiness('http://x:8080/', svc)
        self.assertTrue(ok)
        called_url = phc.call_args[0][0]
        self.assertEqual(called_url, 'http://x:8080/ready')


class TestRunGreenSmoke(TestCase):
    def test_unconfigured_passes(self):
        self.assertEqual(
            guard._run_green_smoke('abc', _service())[0], True)

    def test_zero_exit_passes(self):
        svc = _service(smoke_command='python -c "import app.main"')
        proc = MagicMock(returncode=0, stdout='ok', stderr='')
        with patch('subprocess.run', return_value=proc):
            ok, detail = guard._run_green_smoke('abc', svc)
        self.assertTrue(ok)
        self.assertIn('passed', detail)

    def test_nonzero_fails_with_output(self):
        svc = _service(smoke_command='false')
        proc = MagicMock(returncode=1, stdout='', stderr='boom')
        with patch('subprocess.run', return_value=proc):
            ok, detail = guard._run_green_smoke('abc', svc)
        self.assertFalse(ok)
        self.assertIn('exit 1', detail)
        self.assertIn('boom', detail)

    def test_exec_error_fails(self):
        svc = _service(smoke_command='false')
        with patch('subprocess.run', side_effect=OSError('no docker')):
            ok, _ = guard._run_green_smoke('abc', svc)
        self.assertFalse(ok)
