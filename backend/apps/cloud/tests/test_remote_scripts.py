"""Remote node backup/restore script rendering (no docker, no DB)."""
import json
import re

from django.test import SimpleTestCase

from apps.cloud.services.backup_service.core import (
    build_remote_backup_script,
    build_remote_restore_script,
)


def _echoed_json(script, filename):
    m = re.search(r"echo '(\[.*?\])' > " + re.escape(filename), script)
    assert m, f"{filename} manifest echo missing"
    return json.loads(m.group(1))


class RemoteScriptTests(SimpleTestCase):
    def _addons(self):
        return [
            {'name': 'pg-main', 'container': 'smsly-addon-postgres-1',
             'type': 'POSTGRES'},
            {'name': 'cache', 'container': 'smsly-addon-redis-2',
             'type': 'REDIS'},
            {'name': 'files', 'container': 'smsly-addon-minio-3',
             'type': 'MINIO'},
        ]

    def test_backup_renders_all_blocks(self):
        script = build_remote_backup_script('my svc', self._addons())
        for needle in ('BACKUP_PATH=', 'addon_manifest.json',
                       'volume_manifest.json', 'addon_pg-main_dump.sql',
                       'addon_cache_dump.rdb', 'export MASK_SECRETS=1',
                       '{{range .Config.Env}}'):
            self.assertIn(needle, script)
        # non-DB addon gets no dump block
        self.assertNotIn('addon_files', script)
        self.assertNotIn('minio-3', script)
        manifest = _echoed_json(script, 'addon_manifest.json')
        self.assertEqual(
            [(e['addon'], e['filename']) for e in manifest],
            [('pg-main', 'addon_pg-main_dump.sql'),
             ('cache', 'addon_cache_dump.rdb')])

    def test_transfer_keeps_secrets(self):
        masked = build_remote_backup_script('s', [], mask_secrets=True)
        self.assertIn('export MASK_SECRETS=1', masked)
        plain = build_remote_backup_script('s', [], mask_secrets=False)
        self.assertIn('export MASK_SECRETS=0', plain)

    def test_no_addons_ok(self):
        script = build_remote_backup_script('s', [])
        self.assertIn('no addon DBs to dump', script)
        self.assertEqual(_echoed_json(script, 'addon_manifest.json'), [])

    def test_restore_renders_addon_block(self):
        script = build_remote_restore_script('my svc', '/tmp/x')
        for needle in ('addon_manifest.json', 'addon_*.sql',
                       'restore_addon_dump.sql', 'exit 1',
                       'volume_manifest.json', 'db_dump.sql'):
            self.assertIn(needle, script)

    def test_service_name_quoted(self):
        script = build_remote_backup_script('weird;name|x', [])
        self.assertIn("'weird;name|x'", script)
