# pylint: disable=invalid-name
"""Tests for the hardened `_ensure_docker_driver` buildx flow."""

import subprocess
import threading
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.deployments.services.pipeline import PipelineManager


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeManager:
    """Bare-bones stand-in so we can call PipelineManager._ensure_docker_driver
    without instantiating the real class (which needs a Deployment)."""

    _ensure_docker_driver = PipelineManager._ensure_docker_driver


class EnsureDockerDriverSafetyTests(SimpleTestCase):
    def setUp(self):
        # Reset the class-level lock so a test that crashed mid-call does
        # not poison the next test.
        PipelineManager._buildx_driver_lock = threading.Lock()

    def test_already_docker_driver_is_a_noop(self):
        """If the inspector reports 'Driver: docker', the function returns
        True and never invokes docker buildx rm / create."""
        inspect_result = _completed(
            returncode=0,
            stdout="Name:   default\nDriver: docker\n",
        )
        with patch("subprocess.run", return_value=inspect_result) as mock_run:
            result = _FakeManager()._ensure_docker_driver()
        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 1)

    def test_recreation_succeeds_after_non_docker(self):
        """Non-docker driver triggers rm + create and returns True on success."""
        inspect_result = _completed(
            returncode=0,
            stdout="Name:   default\nDriver: kubernetes\n",
        )
        rm_result = _completed(returncode=0, stdout="removed", stderr="")
        create_result = _completed(returncode=0, stdout="created", stderr="")
        with patch(
            "subprocess.run",
            side_effect=[inspect_result, rm_result, create_result],
        ) as mock_run:
            result = _FakeManager()._ensure_docker_driver()
        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(
            [c.args[0][2] for c in mock_run.call_args_list],
            ["inspect", "rm", "create"],
        )

    def test_non_zero_returncode_on_rm_returns_false_and_logs(self):
        """When `docker buildx rm` returns non-zero, the function returns
        False and logs the failure."""
        inspect_result = _completed(
            returncode=0,
            stdout="Name:   default\nDriver: kubernetes\n",
        )
        rm_result = _completed(returncode=1, stdout="", stderr="boom")
        with patch(
            "subprocess.run",
            side_effect=[inspect_result, rm_result],
        ) as mock_run, \
            self.assertLogs("apps.deployments.services.pipeline", level="ERROR") as cm:
            result = _FakeManager()._ensure_docker_driver()
        self.assertFalse(result)
        self.assertEqual(mock_run.call_count, 2)
        self.assertTrue(
            any("buildx_driver_recreation_failed" in line for line in cm.output),
            cm.output,
        )

    def test_non_zero_returncode_on_create_returns_false_and_logs(self):
        """When `docker buildx create` returns non-zero, the function returns
        False and logs the failure (rm was OK in this scenario)."""
        inspect_result = _completed(
            returncode=0,
            stdout="Driver: kubernetes\n",
        )
        rm_result = _completed(returncode=0, stdout="", stderr="")
        create_result = _completed(returncode=2, stdout="", stderr="denied")
        with patch(
            "subprocess.run",
            side_effect=[inspect_result, rm_result, create_result],
        ) as mock_run, \
            self.assertLogs("apps.deployments.services.pipeline", level="ERROR") as cm:
            result = _FakeManager()._ensure_docker_driver()
        self.assertFalse(result)
        self.assertEqual(mock_run.call_count, 3)
        self.assertTrue(
            any("buildx_driver_recreation_failed" in line for line in cm.output),
            cm.output,
        )

    def test_subprocess_exception_is_caught_and_logged(self):
        """When subprocess.run raises, the function catches and logs it."""
        inspect_result = _completed(
            returncode=0,
            stdout="Driver: kubernetes\n",
        )
        with patch(
            "subprocess.run",
            side_effect=[inspect_result, OSError("docker daemon not reachable")],
        ) as mock_run, \
            self.assertLogs("apps.deployments.services.pipeline", level="ERROR") as cm:
            result = _FakeManager()._ensure_docker_driver()
        self.assertFalse(result)
        self.assertEqual(mock_run.call_count, 2)
        self.assertTrue(
            any("buildx_driver_recreation_failed" in line for line in cm.output),
            cm.output,
        )

    def test_docker_cli_missing_during_inspect_is_a_soft_skip(self):
        """FileNotFoundError on the inspect call is a soft skip: returning
        True (we cannot recreate, but we also cannot prove we need to)."""
        with patch(
            "subprocess.run",
            side_effect=FileNotFoundError("docker"),
        ) as mock_run, \
            self.assertLogs("apps.deployments.services.pipeline", level="WARNING") as cm:
            result = _FakeManager()._ensure_docker_driver()
        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 1)
        self.assertTrue(
            any("buildx_driver_inspect_skipped" in line for line in cm.output),
            cm.output,
        )

    def test_function_is_idempotent_under_concurrent_calls(self):
        """Two threads calling _ensure_docker_driver at the same time must
        be serialized by the class-level lock and produce exactly the
        expected number of subprocess invocations (no rm/create race)."""
        # Pretend the builder is non-docker (use 'kubernetes' — 'docker' is
        # a substring of 'docker-container' and would short-circuit the
        # recreation path) so both threads want to rm/create. With the lock
        # in place, the second thread will observe the freshly-recreated
        # "docker" builder on its inspect call and skip rm/create entirely.
        inspect_then_already_docker = [
            _completed(returncode=0, stdout="Driver: kubernetes\n"),
            _completed(returncode=0, stdout="Driver: docker\n"),
        ]
        rm_result = _completed(returncode=0, stdout="", stderr="")
        create_result = _completed(returncode=0, stdout="", stderr="")

        def side_effect(*args, **kwargs):
            if not inspect_then_already_docker:
                raise AssertionError("inspect called more than expected")
            return_value = inspect_then_already_docker.pop(0)
            if return_value.stdout == "Driver: kubernetes\n":
                # First caller needs to rm + create, return those next.
                side_effect.next = [rm_result, create_result]
            return return_value

        call_log = []

        def spy_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            call_log.append(cmd[2] if len(cmd) > 2 else "?")
            if hasattr(side_effect, "next") and side_effect.next:
                return side_effect.next.pop(0)
            return side_effect(*args, **kwargs)

        with patch("subprocess.run", side_effect=spy_run):
            results = []
            errors = []
            barrier = threading.Barrier(2)

            def worker():
                try:
                    barrier.wait(timeout=2)
                    r = _FakeManager()._ensure_docker_driver()
                    results.append(r)
                except Exception as exc:  # pragma: no cover - test only
                    errors.append(exc)

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            self.assertFalse(errors, msg=errors)
            self.assertEqual(results, [True, True])
            # We expect: inspect (x2), rm (x1), create (x1). Never rm+create
            # twice — that would be the dangerous interleaving.
            self.assertEqual(call_log.count("rm"), 1)
            self.assertEqual(call_log.count("create"), 1)
            self.assertEqual(call_log.count("inspect"), 2)
