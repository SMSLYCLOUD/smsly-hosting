"""CLI addon types: config, entrypoint, shared-container helpers (mocked docker)."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.addons.services import cli_addons as cli
from apps.deployments.models import Addon, Service

User = get_user_model()

EXPECTED_TYPES = {
    'OPENCODE', 'COMMANDCODE', 'ANTIGRAVITYCLI', 'KIMCHI',
    'FORGECODE', 'DEEPAGENTS', 'QWENCODE', 'FACTORYDROID',
}


class RegistryTests(SimpleTestCase):
    def test_all_eight_types_registered(self):
        self.assertEqual(set(cli.CLI_ADDON_TYPES), EXPECTED_TYPES)

    def test_generic_config_shape(self):
        for type_ in EXPECTED_TYPES:
            cfg = cli.generic_config(type_)
            self.assertEqual(cfg['image'], cli.CLI_BASE_IMAGE)
            self.assertEqual(cfg['port'], cli.CLI_STATUS_PORT)
            # Exposable status/API page (internal until exposed).
            self.assertEqual(cfg['dashboard_port'], cli.CLI_STATUS_PORT)
            self.assertFalse(cfg['auth'])
            self.assertIn(cli.cli_binary(type_), cfg['ready_cmd'])
            self.assertEqual(cfg['command'][:2], ['bash', '-c'])

    def test_generic_config_rejects_unknown(self):
        with self.assertRaises(ValueError):
            cli.generic_config('POSTGRES')

    def test_entrypoint_installs_all_clis(self):
        # One shared container per service: the entrypoint installs
        # every CLI idempotently, not just one.
        script = cli.entrypoint_script('OPENCODE')
        for needle in (
                'https://opencode.ai/install',
                'command-code@latest',
                'antigravity.google/cli/install.sh',
                'getkimchi/kimchi',
                'https://forgecode.dev/cli',
                'astral.sh/uv/install.sh',
                'deepagents-cli',
                '@qwen-code/qwen-code',
                'npm i -g droid@latest',
                'cli-status.js'):
            self.assertIn(needle, script)

    def test_entrypoint_shared_regardless_of_type(self):
        self.assertEqual(cli.entrypoint_script('OPENCODE'),
                         cli.entrypoint_script('QWENCODE'))

    def test_entrypoint_links_binaries_onto_default_path(self):
        # `docker exec` uses the default PATH (no ~/.opencode/bin,
        # ~/.local/bin, /data/bin) — without symlinks the console and
        # probes cannot resolve the CLIs. `case` (not `[ != ]`) because
        # dash chokes on unquoted globs and would skip every link.
        script = cli.entrypoint_script()
        self.assertIn('/usr/local/bin/', script)
        self.assertIn('case "$_p" in', script)
        for type_ in EXPECTED_TYPES:
            self.assertIn(cli.cli_binary(type_), script)

    def test_status_specs_cover_all_types(self):
        specs = json.loads(cli.status_specs_json())
        self.assertEqual({s['type'] for s in specs}, EXPECTED_TYPES)
        for spec in specs:
            self.assertTrue(spec['binary'])
            self.assertTrue(spec['ready'])


class ConfigValidationTests(SimpleTestCase):
    def test_rejects_non_cli_type(self):
        with self.assertRaises(ValueError):
            cli.validate_cli_config('POSTGRES', {})

    def test_rejects_unsafe_values(self):
        with self.assertRaises(ValueError):
            cli.validate_cli_config('OPENCODE', {'api_key_env': 'bad-name!'})
        with self.assertRaises(ValueError):
            cli.validate_cli_config('OPENCODE', {'model': 'x y z'})
        with self.assertRaises(ValueError):
            cli.validate_cli_config('OPENCODE', {'api_key': 'has\nnewline'})

    def test_partial_update_and_clear_semantics(self):
        merged = cli.merge_stored_config({}, {'model': 'anthropic/x'})
        self.assertEqual(merged, {'model': 'anthropic/x'})
        # Omitted api_key keeps the stored one.
        merged = cli.merge_stored_config(
            {'api_key': 'k', 'model': 'm'}, {'model': 'm2'})
        self.assertEqual(merged['api_key'], 'k')
        self.assertEqual(merged['model'], 'm2')
        # Empty api_key clears.
        merged = cli.merge_stored_config(
            {'api_key': 'k'}, {'api_key': ''})
        self.assertNotIn('api_key', merged)

    def test_public_view_masks_secret(self):
        view = cli.public_view('OPENCODE', {'api_key': 's3cret', 'model': 'a/b'})
        self.assertTrue(view['api_key_set'])
        self.assertNotIn('s3cret', json.dumps(view))
        self.assertEqual(view['model'], 'a/b')

    def test_suggest_key_env(self):
        self.assertEqual(cli.suggest_key_env('OPENCODE', 'anthropic/x'),
                         'ANTHROPIC_API_KEY')
        self.assertEqual(cli.suggest_key_env('OPENCODE', 'openrouter/y'),
                         'OPENROUTER_API_KEY')
        self.assertEqual(cli.suggest_key_env('COMMANDCODE', 'anything'),
                         'COMMAND_CODE_API_KEY')
        self.assertEqual(cli.suggest_key_env('KIMCHI', 'kimi-k3'),
                         'KIMCHI_API_KEY')
        self.assertEqual(cli.suggest_key_env('QWENCODE', ''),
                         cli.default_key_env('QWENCODE'))


class ContainerFilesTests(SimpleTestCase):
    def test_opencode_model_file(self):
        files = cli.container_files('OPENCODE', {'model': 'anthropic/x'})
        doc = json.loads(files[cli.OPENCODE_JSON])
        self.assertEqual(doc['model'], 'anthropic/x')
        self.assertIn('$schema', doc)

    def test_commandcode_merges_later(self):
        files = cli.container_files(
            'COMMANDCODE', {'model': 'm', 'provider': 'codex'})
        doc = json.loads(files[cli.COMMANDCODE_JSON])
        self.assertEqual(doc, {'model': 'm', 'provider': 'codex'})

    def test_kimchi_key_file(self):
        files = cli.container_files('KIMCHI', {'api_key': 'k'})
        self.assertEqual(json.loads(files[cli.KIMCHI_JSON]), {'apiKey': 'k'})

    def test_factory_model_file(self):
        files = cli.container_files('FACTORYDROID', {'model': 'm1'})
        self.assertEqual(json.loads(files[cli.FACTORY_JSON]), {'model': 'm1'})

    def test_interactive_types_have_no_files(self):
        for type_ in ('ANTIGRAVITYCLI', 'FORGECODE', 'DEEPAGENTS', 'QWENCODE'):
            self.assertEqual(cli.container_files(type_, {'model': 'm'}), {})

    def test_provision_env(self):
        self.assertEqual(
            cli.provision_env({'api_key': 'k', 'api_key_env': 'X_KEY'}),
            {'X_KEY': 'k'})
        self.assertEqual(cli.provision_env({'api_key': 'k'}), {})
        self.assertEqual(cli.provision_env({}), {})


class PushFilesTests(SimpleTestCase):
    def test_push_skips_when_no_files(self):
        with mock.patch.object(cli, '_docker') as docker:
            cli.push_cli_files('c1', 'QWENCODE', {'model': 'm'})
            docker.assert_not_called()

    def test_push_writes_via_stdin_never_argv(self):
        seen = []

        def fake_docker(*args, input_bytes=None):
            seen.append((args, input_bytes))
            if args[:2] == ('exec', 'c1') and args[2] == 'true':
                return 0, ''
            if 'cat >' in (args[-1] if args else ''):
                return 0, ''
            if args[-2:] == ('mkdir', '-p'):
                return 0, ''
            return 0, ''

        with mock.patch.object(cli, '_docker', side_effect=fake_docker):
            cli.push_cli_files('c1', 'OPENCODE', {'model': 'a/b'})
        writes = [s for s in seen if s[0][-1].startswith('cat >')]
        self.assertEqual(len(writes), 1)
        self.assertIn(b'"model": "a/b"', writes[0][1])
        # Secret-bearing content only ever travels on stdin.
        for args, _ in seen:
            self.assertNotIn('s3cret-never-set', ' '.join(args))

    def test_push_unreachable_raises(self):
        with mock.patch.object(cli, '_docker', return_value=(1, 'nope')):
            with self.assertRaises(RuntimeError):
                cli.push_cli_files('c1', 'OPENCODE', {'model': 'a/b'})


class SharedContainerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="clishare", password="x")
        self.service = Service.objects.create(name="clisvc", owner=self.user)

    def _addon(self, type_, name, **kwargs):
        defaults = dict(
            service=self.service, name=name, addon_type=type_,
            status=Addon.Status.ACTIVE, provision_mode='container',
            connection_url=f"http://{name}:8686/")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def test_shared_name_stable_per_service(self):
        a = self._addon('OPENCODE', 'opencode-x')
        b = self._addon('QWENCODE', 'qwen-x')
        self.assertEqual(cli.resolve_container_name(a),
                         cli.resolve_container_name(b))
        self.assertIn(str(self.service.id), cli.resolve_container_name(a))

    def test_non_cli_keeps_per_addon_name(self):
        a = self._addon('REDIS', 'redis-x')
        self.assertIn(
            f"redis-{a.id}", cli.resolve_container_name(a))

    def test_siblings_and_routers(self):
        a = self._addon('OPENCODE', 'opencode-x')
        self._addon('QWENCODE', 'qwen-x', public_domain='q.example.com')
        self._addon('REDIS', 'redis-x')
        sibs = cli.sibling_cli_addons(a)
        self.assertEqual([s.addon_type for s in sibs], ['QWENCODE'])
        routers = cli.exposed_routers(a)
        self.assertEqual(len(routers), 1)
        self.assertEqual(routers[0][1], 'q.example.com')
        self.assertEqual(cli.sibling_aliases(a), ['opencode-x', 'qwen-x'])

    def test_merged_env_unions_sibling_keys(self):
        import json as _json
        self._addon('OPENCODE', 'opencode-x')
        b = self._addon('KIMCHI', 'kimchi-x')
        b.cli_config = _json.dumps(
            {'api_key': 'k', 'api_key_env': 'KIMCHI_API_KEY'})
        b.save(update_fields=['cli_config'])
        env = cli.merged_cli_env(self.service)
        self.assertEqual(env, {'KIMCHI_API_KEY': 'k'})

    def test_delete_shared_keeps_container_with_siblings(self):
        a = self._addon('OPENCODE', 'opencode-x')
        self._addon('QWENCODE', 'qwen-x')
        with mock.patch.object(cli, 'detach_cli_alias') as detach:
            ok = cli.delete_shared_resources(a, mock.Mock())
        self.assertTrue(ok)
        detach.assert_called_once_with(a)

    def test_delete_shared_removes_container_when_last(self):
        a = self._addon('OPENCODE', 'opencode-x')
        remover = mock.Mock(return_value=True)
        with mock.patch.object(cli, 'detach_cli_alias'), \
             mock.patch('subprocess.run') as run:
            ok = cli.delete_shared_resources(a, remover)
        self.assertTrue(ok)
        remover.assert_called_once_with(cli.resolve_container_name(a))
        run.assert_called_once()
