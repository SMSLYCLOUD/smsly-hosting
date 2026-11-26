"""Tenant builds must never reach a daemon without verified resource caps."""
import json
import os
import subprocess
import tempfile
import tomllib
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.deployments.services.pipeline import build as build_mod
from apps.deployments.services.pipeline.exceptions import BuildError


class BuilderContainmentTests(TestCase):
    def lifecycle(self, *, fail=None, memory=512 * 1024 * 1024, quota=150000):
        commands = []
        def run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:3] == ['docker', 'buildx', 'create']:
                opts = [cmd[i + 1] for i, arg in enumerate(cmd[:-1]) if arg == '--driver-opt']
                self.assertIn('memory=512m', opts)
                self.assertIn('memory-swap=512m', opts)
                self.assertIn('cpu-period=100000', opts)
                self.assertIn('cpu-quota=150000', opts)
                self.assertIn('network=smsly-build-net', opts)
                config_path = cmd[cmd.index('--buildkitd-config') + 1]
                with open(config_path, 'rb') as config:
                    registry_config = tomllib.load(config)
                self.assertEqual(
                    registry_config.get('registry'),
                    {'registry:5000': {'insecure': True}},
                )
            if cmd[:3] == ['docker', 'buildx', 'inspect']:
                self.assertIn('--bootstrap', cmd)
            if cmd[:2] == ['docker', 'inspect']:
                self.assertTrue(any('--bootstrap' in c for c in commands))
                output = json.dumps([{'State': {'Running': True}, 'HostConfig': {
                    'Memory': memory, 'MemorySwap': 512 * 1024 * 1024,
                    'CpuPeriod': 100000, 'CpuQuota': quota}}])
            else:
                output = 'ok'
            if fail and fail in cmd:
                return subprocess.CompletedProcess(cmd, 1, '', 'denied')
            return subprocess.CompletedProcess(cmd, 0, output, '')
        return commands, run

    def test_builder_bootstraps_with_caps_and_verifies_daemon_state(self):
        commands, run = self.lifecycle()
        with patch.object(build_mod, '_local_registry_host', return_value='registry:5000'), patch.object(build_mod.subprocess, 'run', side_effect=run):
            self.assertEqual(build_mod._create_ephemeral_builder('limited', 512, 150), 'limited')
        self.assertTrue(any(c[:2] == ['docker', 'inspect'] for c in commands))
        self.assertFalse(any(c[:2] == ['docker', 'update'] for c in commands))

    def test_bootstrap_failure_removes_builder(self):
        commands, run = self.lifecycle(fail='--bootstrap')
        with patch.object(build_mod, '_local_registry_host', return_value='registry:5000'), patch.object(build_mod.subprocess, 'run', side_effect=run):
            self.assertIsNone(build_mod._create_ephemeral_builder('limited', 512, 150))
        self.assertIn(['docker', 'buildx', 'rm', '-f', 'limited'], commands)

    def test_missing_daemon_limit_rejects_builder(self):
        commands, run = self.lifecycle(memory=0)
        with patch.object(build_mod, '_local_registry_host', return_value='registry:5000'), patch.object(build_mod.subprocess, 'run', side_effect=run):
            self.assertIsNone(build_mod._create_ephemeral_builder('limited', 512, 150))
        self.assertIn(['docker', 'buildx', 'rm', '-f', 'limited'], commands)

    def test_missing_cpu_limit_rejects_builder(self):
        commands, run = self.lifecycle(quota=0)
        with patch.object(build_mod, '_local_registry_host', return_value='registry:5000'), patch.object(build_mod.subprocess, 'run', side_effect=run):
            self.assertIsNone(build_mod._create_ephemeral_builder('limited', 512, 150))
        self.assertIn(['docker', 'buildx', 'rm', '-f', 'limited'], commands)

    def test_infrastructure_failure_never_calls_uncapped_sdk(self):
        mixin = build_mod.BuildMixin()
        mixin.deployment = SimpleNamespace(id='deployment')
        with (patch.object(mixin, '_build_with_buildkit', side_effect=RuntimeError('cannot connect to the Docker daemon')),
              patch.object(mixin, '_build_via_docker_py') as sdk):
            with self.assertRaises(BuildError):
                mixin._build_with_docker_engine('.', 'Dockerfile', 'tenant:tag', {}, [], {})
        sdk.assert_not_called()

    def test_legacy_env_flags_cannot_disable_containment(self):
        mixin = build_mod.BuildMixin()
        with (patch.dict(os.environ, {'SMSLY_DOCKER_BUILDER': 'classic', 'SMSLY_BUILDER_EPHEMERAL': '0'}),
                patch.object(mixin, '_build_with_buildkit') as contained,
                patch.object(mixin, '_build_via_docker_py') as sdk):
            mixin._build_with_docker_engine('.', 'Dockerfile', 'tenant:tag', {}, [], {})
        contained.assert_called_once()
        sdk.assert_not_called()

    def test_loopback_registry_refuses_with_actionable_error(self):
        mixin = build_mod.BuildMixin()
        mixin.deployment = SimpleNamespace(id='deployment')
        mixin.secret_values = []
        with patch.object(build_mod, '_create_ephemeral_builder') as create:
            with self.assertRaisesRegex(BuildError, 'registry:5000'):
                mixin._build_with_buildkit('.', 'Dockerfile', 'localhost:5000/tenant:tag', {}, [], {})
        create.assert_not_called()

    def test_failed_setup_stops_before_build_and_cleans_secrets(self):
        mixin = build_mod.BuildMixin()
        mixin.deployment = SimpleNamespace(id='deployment')
        mixin.secret_values = []
        with tempfile.TemporaryDirectory() as context:
            dockerfile = os.path.join(context, 'Dockerfile')
            with open(dockerfile, 'w') as stream:
                stream.write('FROM scratch\n')
            with (patch.object(build_mod, '_create_ephemeral_builder', return_value=None),
                patch.object(build_mod, '_build_limits', return_value=(512, 150, 30)),
                patch.object(build_mod, '_cleanup_secret_files') as cleanup,
                patch.object(build_mod.subprocess, 'run') as run):
                with self.assertRaises(BuildError):
                    mixin._build_with_buildkit(context, dockerfile, 'tenant:tag', {}, [], {'token': 'dummy'})
            run.assert_not_called()
            paths = cleanup.call_args.args[0]
            self.assertEqual(len(paths), 1)
            for path in paths:
                os.remove(path)
