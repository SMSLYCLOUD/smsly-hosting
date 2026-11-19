"""Unit tests for ephemeral per-build builders (no DB, no Docker)."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.pipeline import build as build_mod


def _mixin():
    mixin = SimpleNamespace(
        deployment=SimpleNamespace(id='dep-12345678'),
        secret_values={},
        append_log_calls=[],
    )
    # Plain namespace (not a BuildMixin): stub the daemon-store check.
    mixin._verify_built_tag = lambda tag: True
    return mixin


def _ok_proc():
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = 'ok'
    proc.stderr = ''
    return proc


class EphemeralBuilderTests(TestCase):
    def _run(self, ephemeral):
        mixin = _mixin()
        with patch.object(
            build_mod, 'append_log',
            side_effect=lambda dep, msg: mixin.append_log_calls.append(msg),
        ), patch('subprocess.run', return_value=_ok_proc()) as mock_run, \
                patch.object(build_mod, '_ephemeral_builder_enabled',
                             return_value=ephemeral), \
                patch.object(build_mod, '_create_ephemeral_builder',
                             return_value='smsly-eph-dep-1234') as mk, \
                patch.object(build_mod, '_remove_ephemeral_builder') as rm, \
                patch.object(build_mod, '_build_limits',
                             return_value=(10240, 400, 1800)), \
                patch.object(build_mod.BuildMixin, '_verify_built_tag',
                             return_value=True), \
                patch('builtins.open',
                      MagicMock(return_value=MagicMock(
                          __enter__=MagicMock(return_value=MagicMock(
                              read=MagicMock(return_value='FROM x\n'))),
                          __exit__=MagicMock(return_value=False)))):
            build_mod.BuildMixin._build_with_buildkit(
                mixin, context_dir='/tmp/ctx', dockerfile_path='/tmp/ctx/Dockerfile',
                tag='registry:5000/smsly/a:aaa1111', buildargs={},
                cache_from=[], secrets={},
            )
        return mock_run, mixin

    def test_disabled_legacy_flag_still_uses_contained_builder(self):
        mock_run, _ = self._run(False)
        cmds = [c.args[0] for c in mock_run.call_args_list]
        build_cmd = next(c for c in cmds if 'build' in c)
        self.assertEqual(build_cmd[:3], ['docker', 'buildx', 'build'])
        self.assertIn('--builder', build_cmd)
        self.assertIn('smsly-eph-dep-1234', build_cmd)
        self.assertIn('type=docker', build_cmd)

    def test_ephemeral_path_uses_builder_and_output(self):
        mock_run, _ = self._run(True)
        cmds = [c.args[0] for c in mock_run.call_args_list]
        build_cmds = [c for c in cmds if 'build' in c]
        self.assertEqual(len(build_cmds), 1)
        flat = ' '.join(a for a in build_cmds[0] if isinstance(a, str))
        self.assertIn('buildx', flat)
        self.assertIn('--builder', flat)
        self.assertIn('smsly-eph-dep-1234', flat)
        self.assertIn('--output', flat)
        self.assertIn('type=docker', flat)
        self.assertNotIn('--load', flat)

    def test_create_remove_helpers(self):
        from .test_build_containment import BuilderContainmentTests
        regression = BuilderContainmentTests()
        commands, run = regression.lifecycle()
        with patch.object(build_mod, '_local_registry_host', return_value='registry:5000'), patch('subprocess.run', side_effect=run):
            self.assertEqual(build_mod._create_ephemeral_builder('limited', 512, 150), 'limited')
            build_mod._remove_ephemeral_builder('limited')
        self.assertIn(['docker', 'buildx', 'rm', '-f', 'limited'], commands)

    def test_create_failure_returns_none(self):
        bad = MagicMock(returncode=1, stdout='', stderr='nope')
        with patch('subprocess.run', return_value=bad):
            self.assertIsNone(build_mod._create_ephemeral_builder('n', 1024, 100))

    def test_flag_env_parsing(self):
        with patch.dict('os.environ', {'SMSLY_BUILDER_EPHEMERAL': '1'}):
            self.assertTrue(build_mod._ephemeral_builder_enabled())
        with patch.dict('os.environ', {}, clear=False):
            import os as _os
            _os.environ.pop('SMSLY_BUILDER_EPHEMERAL', None)
            self.assertTrue(build_mod._ephemeral_builder_enabled())
