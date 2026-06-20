# pylint: disable=invalid-name
"""
Regression tests for the buildx default-builder self-heal (Batch J).

Covers:
  1. ``_is_buildx_default_broken`` correctly detects the
     ``failed to recreate the buildx default builder`` error in
     Nixpacks/BuildKit stderr.
  2. ``_ensure_buildx_fallback`` creates a docker-container
     fallback builder if one doesn't exist.
  3. ``build_image`` retries with ``BUILDX_BUILDER=<fallback>``
     when the build fails with the buildx default-builder
     recreation error.
  4. The fallback name is read from settings.
  5. The self-healing orchestrator classifies buildx failures
     and executes the REPAIR_BUILDX action.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings


class IsBuildxDefaultBrokenTests(SimpleTestCase):
    """The detector must NOT false-positive on plain build
    failures and MUST true-positive on the specific
    buildx default-builder recreation error.
    """

    def _call(self, stderr):
        from apps.cloud.services.builder import _is_buildx_default_broken
        return _is_buildx_default_broken(stderr)

    def test_empty_stderr_is_not_buildx_broken(self):
        self.assertFalse(self._call(''))
        self.assertFalse(self._call(None))

    def test_nixpacks_generic_error_is_not_buildx_broken(self):
        # Common Nixpacks error: a missing dependency
        self.assertFalse(self._call(
            'Error: failed to build: npm install failed\n'
            'Could not resolve "react"'
        ))

    def test_docker_daemon_error_is_not_buildx_broken(self):
        self.assertFalse(self._call(
            'Cannot connect to the Docker daemon at unix:///var/run/docker.sock'
        ))

    def test_canonical_buildx_default_broken(self):
        stderr = (
            'Refusing to build: failed to recreate the buildx default '
            'builder with the docker driver. See server logs for the '
            'underlying docker buildx error.'
        )
        self.assertTrue(self._call(stderr))

    def test_buildx_default_builder_keyword(self):
        # The marker fragment alone is enough — the full
        # buildx stack-trace may be missing in some Nixpacks
        # output variants.
        self.assertTrue(self._call('error: buildx default builder corrupted'))

    def test_no_such_builder_default(self):
        # Buildx variant of "no such builder: default"
        self.assertTrue(self._call(
            "ERROR: no such builder: default"
        ))

    def test_case_insensitive_match(self):
        # Nixpacks may emit uppercase or mixed-case BuildKit
        # output. The detector must be case-insensitive.
        self.assertTrue(self._call(
            'FAILED TO RECREATE THE BUILDX DEFAULT BUILDER'
        ))


class EnsureBuildxFallbackTests(SimpleTestCase):
    """``_ensure_buildx_fallback`` must:
      - return (True, ...) when the fallback already exists,
      - return (True, ...) after creating the fallback,
      - return (False, ...) on docker daemon error.
    """

    def _call(self, name='smsly-fallback'):
        from apps.cloud.services.builder import _ensure_buildx_fallback
        return _ensure_buildx_fallback(name)

    def test_already_exists(self):
        ls_proc = mock.Mock(returncode=0, stdout='mybuilder*\ndefault')
        with mock.patch(
            'subprocess.run',
            side_effect=[ls_proc, mock.Mock(returncode=0, stdout='')],
        ):
            created, status = self._call('mybuilder')
        self.assertTrue(created)
        self.assertIn('already exists', status)

    def test_create_when_missing(self):
        ls_proc = mock.Mock(returncode=0, stdout='default')  # not fallback
        create_proc = mock.Mock(returncode=0, stdout='')
        with mock.patch(
            'subprocess.run',
            side_effect=[ls_proc, create_proc],
        ) as run_mock:
            created, status = self._call('smsly-fallback')
        self.assertTrue(created)
        self.assertIn('created', status)
        # Verify the create invocation used the docker-container driver
        create_call = run_mock.call_args_list[1]
        cmd = create_call[0][0]
        self.assertIn('buildx', cmd)
        self.assertIn('create', cmd)
        self.assertIn('smsly-fallback', cmd)
        self.assertIn('docker-container', cmd)
        self.assertIn('--use', cmd)

    def test_docker_ls_failure(self):
        # If ``docker buildx ls`` itself fails (e.g. no docker
        # binary), the helper returns False so the caller
        # can fall through to the original error.
        err = FileNotFoundError(2, 'No such file or directory', 'docker')
        with mock.patch('subprocess.run', side_effect=err):
            created, status = self._call('smsly-fallback')
        self.assertFalse(created)
        self.assertIn('buildx ls failed', status)

    def test_create_failure(self):
        ls_proc = mock.Mock(returncode=0, stdout='default')
        create_proc = mock.Mock(
            returncode=1,
            stdout='',
            stderr='docker: error: could not create builder',
        )
        with mock.patch(
            'subprocess.run',
            side_effect=[ls_proc, create_proc],
        ):
            created, status = self._call('smsly-fallback')
        self.assertFalse(created)
        self.assertIn('fallback create failed', status)
        self.assertIn('could not create builder', status)


class BuildxFallbackBuilderNameTests(SimpleTestCase):
    """The fallback name must be read from settings
    (BUILDX_FALLBACK_BUILDER) with a default of 'smsly-fallback'.
    """

    def test_default_name(self):
        from apps.cloud.services.builder import _buildx_fallback_builder_name
        # Without BUILDX_FALLBACK_BUILDER set, the helper
        # returns 'smsly-fallback'.
        with override_settings(BUILDX_FALLBACK_BUILDER=None):
            self.assertEqual(
                _buildx_fallback_builder_name(), 'smsly-fallback'
            )

    def test_empty_string_falls_back_to_default(self):
        from apps.cloud.services.builder import _buildx_fallback_builder_name
        with override_settings(BUILDX_FALLBACK_BUILDER=''):
            self.assertEqual(
                _buildx_fallback_builder_name(), 'smsly-fallback'
            )

    def test_custom_name(self):
        from apps.cloud.services.builder import _buildx_fallback_builder_name
        with override_settings(BUILDX_FALLBACK_BUILDER='my-fallback'):
            self.assertEqual(
                _buildx_fallback_builder_name(), 'my-fallback'
            )


class BuildImageSelfHealTests(SimpleTestCase):
    """``build_image`` must:
      - raise a normal RuntimeError on a non-buildx failure,
      - create the fallback and retry when stderr matches the
        buildx default-builder recreation error.
    """

    def setUp(self):
        import os
        import tempfile
        # Create a real source dir so the early existence
        # check in build_image passes; the actual Nixpacks
        # invocation is mocked.
        self._tmpdir = tempfile.mkdtemp(prefix='smsly-buildx-test-')
        # Create a dummy Dockerfile so Nixpacks wouldn't
        # immediately fail on missing source files (though
        # the subprocess is mocked anyway).
        with open(os.path.join(self._tmpdir, 'Procfile'), 'w') as f:
            f.write('web: echo hello\n')
        # Capture a reference to the real CalledProcessError
        # class BEFORE any test patches ``subprocess`` (the
        # build_image code path imports ``subprocess`` at
        # call time via the module attribute, so a test-side
        # ``mock.patch.object(builder, 'subprocess', ...)``
        # would replace the class with a MagicMock that
        # Python refuses to ``except``).
        import subprocess as _sp
        self._real_CalledProcessError = _sp.CalledProcessError

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _fail_with(self, stderr):
        """Return a mock subprocess.run that fails the first
        invocation with the given stderr.
        """
        exc = self._real_CalledProcessError(
            returncode=1,
            cmd=['nixpacks', 'build', '/tmp/src'],
            output='',
            stderr=stderr,
        )
        return mock.Mock(side_effect=exc)

    def test_non_buildx_failure_raises_immediately(self):
        from apps.cloud.services import builder
        # First call (the build) fails with a generic error
        # that is NOT the buildx default-builder recreation
        # error. The helper must NOT attempt a fallback.
        fail = self._fail_with(
            'Nixpacks build failed: cannot find package "react"'
        )
        with mock.patch.object(builder, 'shutil') as shutil_mock, \
             mock.patch.object(builder, 'subprocess') as sp:
            shutil_mock.which.return_value = '/usr/bin/nixpacks'
            sp.run.side_effect = fail
            with self.assertRaises(RuntimeError) as cm:
                builder.NixpacksBuilder.build_image(
                    source_dir=self._tmpdir,
                    image_name='test:latest',
                )
        self.assertIn('cannot find package', str(cm.exception))

    def test_buildx_failure_triggers_fallback_retry(self):
        from apps.cloud.services import builder
        # First call: build fails with the buildx default
        # error. Second call (the retry) succeeds. The
        # _ensure_buildx_fallback helper is called once.
        buildx_stderr = (
            'Refusing to build: failed to recreate the buildx '
            'default builder with the docker driver.'
        )
        first_exc = self._real_CalledProcessError(
            returncode=1, cmd=['nixpacks'], output='', stderr=buildx_stderr,
        )
        success = mock.Mock(
            returncode=0, stdout='image built', stderr='',
        )
        # _ensure_buildx_fallback: returns (True, 'created...')
        # We mock the underlying subprocess inside it.
        ls_proc = mock.Mock(returncode=0, stdout='default')
        create_proc = mock.Mock(returncode=0, stdout='')
        with mock.patch.object(builder, 'shutil') as shutil_mock, \
             mock.patch.object(builder, 'subprocess') as sp:
            shutil_mock.which.return_value = '/usr/bin/nixpacks'
            # First: build fails. Second: ls. Third: create.
            # Fourth: retry build (succeeds).
            sp.run.side_effect = [
                first_exc, ls_proc, create_proc, success,
            ]
            result = builder.NixpacksBuilder.build_image(
                source_dir=self._tmpdir,
                image_name='test:latest',
            )
        self.assertEqual(result['image_name'], 'test:latest')
        # The retry must have used BUILDX_BUILDER=smsly-fallback
        retry_call = sp.run.call_args_list[3]
        retry_env = retry_call[1]['env']
        self.assertEqual(retry_env.get('BUILDX_BUILDER'), 'smsly-fallback')

    def test_buildx_failure_fallback_creation_fails_still_raises(self):
        from apps.cloud.services import builder
        # The build fails with the buildx error. The fallback
        # creation also fails. The helper must surface the
        # original error (not a misleading "fallback failed"
        # message).
        buildx_stderr = (
            'Refusing to build: failed to recreate the buildx '
            'default builder with the docker driver.'
        )
        first_exc = self._real_CalledProcessError(
            returncode=1, cmd=['nixpacks'], output='', stderr=buildx_stderr,
        )
        # buildx ls fails (no docker) -> _ensure_buildx_fallback
        # returns (False, 'buildx ls failed: ...')
        with mock.patch.object(builder, 'shutil') as shutil_mock, \
             mock.patch.object(builder, 'subprocess') as sp:
            shutil_mock.which.return_value = '/usr/bin/nixpacks'
            sp.run.side_effect = [first_exc, FileNotFoundError(2, 'docker')]
            with self.assertRaises(RuntimeError) as cm:
                builder.NixpacksBuilder.build_image(
                    source_dir=self._tmpdir,
                    image_name='test:latest',
                )
        # The error should still mention the buildx error
        # (the original cause), not the fallback failure.
        self.assertIn('failed to recreate', str(cm.exception))


class OrchestratorBuildxHealTests(TestCase):
    """The self-healing orchestrator must:
      - classify ``failed to recreate the buildx default builder``
        logs as ``FailureType.BUILDX_BROKEN``,
      - suggest ``RecoveryAction.REPAIR_BUILDX``,
      - dispatch the ``_repair_buildx`` action which creates
        the fallback on the node.
    """

    def test_logs_with_buildx_error_are_classified(self):
        from apps.deployments.services.self_healing_orchestrator import (
            SelfHealingOrchestrator,
        )
        orchestrator = SelfHealingOrchestrator.__new__(
            SelfHealingOrchestrator
        )
        self.assertTrue(orchestrator._looks_like_buildx_failure(
            'failed to recreate the buildx default builder with the docker driver'
        ))

    def test_logs_without_buildx_keyword_are_not_classified(self):
        from apps.deployments.services.self_healing_orchestrator import (
            SelfHealingOrchestrator,
        )
        orchestrator = SelfHealingOrchestrator.__new__(
            SelfHealingOrchestrator
        )
        self.assertFalse(orchestrator._looks_like_buildx_failure(
            'Error: failed to build: npm install failed'
        ))

    def test_repair_buildx_creates_fallback(self):
        # _repair_buildx is an SSH-driven action. We
        # instantiate the orchestrator with a mock SSH
        # surface (the orchestrator accepts the SSH client
        # as a constructor arg or creates one internally).
        from apps.deployments.services.self_healing_orchestrator import (
            RecoveryAction,
            SelfHealingOrchestrator,
        )
        # Mock the orchestrator's _exec helper to simulate
        # the SSH-side shell commands. _exec returns (stdout,
        # stderr, returncode).
        with mock.patch.object(
            SelfHealingOrchestrator, '_can_heal', return_value=True
        ), mock.patch.object(
            SelfHealingOrchestrator, '_exec',
            return_value=('smsly-fallback*\n', '', 0),
        ) as exec_mock, mock.patch.object(
            SelfHealingOrchestrator, '_log', return_value=None,
        ):
            orchestrator = SelfHealingOrchestrator.__new__(
                SelfHealingOrchestrator
            )
            with override_settings(
                BUILDX_FALLBACK_BUILDER='smsly-fallback'
            ):
                result = orchestrator._repair_buildx()
        self.assertEqual(result.action_taken, RecoveryAction.REPAIR_BUILDX)
        self.assertTrue(result.success)
        # The orchestrator must have invoked the docker CLI
        # in this order: create fallback, use fallback,
        # remove default context, remove default builder,
        # verify with buildx ls.
        call_args_list = [c[0][0] for c in exec_mock.call_args_list]
        joined = ' || '.join(call_args_list)
        self.assertIn('buildx create', joined)
        self.assertIn('docker-container', joined)
        self.assertIn('context rm default', joined)
        self.assertIn('buildx rm default', joined)
        self.assertIn('buildx ls', joined)
