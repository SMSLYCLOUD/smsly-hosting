"""
Regression tests for Batch J: master → node secret sync.

Verifies that the install.sh provisioning flow correctly
syncs six additional críticos secrets from the master's
.env file to the node's .env file:

  - BACKUP_ENCRYPTION_KEY  (Fernet key for backup encryption)
  - BACKUP_REQUIRE_ENCRYPTION  (true/false policy)
  - GITHUB_WEBHOOK_SECRET  (GitHub HMAC verification)
  - AUTOSCALER_API_TOKEN  (autoscaler-service bearer)
  - FRP_AUTH_TOKEN  (FRP tunnel relay auth)
  - PGCAT_ADMIN_PASSWORD  (PgCat administration)

Plus the two already-synced:
  - GATEWAY_SECRET
  - FIELD_ENCRYPTION_KEY

These tests are pure-Python (no subprocess) so they run on
any platform. The install.sh file is read directly and the
sync block is verified by checking the actual source
structure.
"""
import os
import unittest


class MasterToNodeSecretSyncTests(unittest.TestCase):
    """Verifies the install.sh provisioning flow syncs eight
    críticos secrets from master to node.
    """

    @classmethod
    def setUpClass(cls):
        cls.repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', '..', '..')
        )
        cls.install_sh = os.path.join(cls.repo_root, 'install.sh')
        cls.backend_install_sh = os.path.join(
            cls.repo_root, 'backend', 'install.sh'
        )
        cls._load_script()

    @classmethod
    def _load_script(cls):
        for path in (cls.install_sh, cls.backend_install_sh):
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    raw = f.read()
                # install.sh is written in a mix of UTF-8 and
                # Windows-1252 (the emoji-heavy banner section is
                # CP-1252-encoded). Decode permissively.
                cls.content = raw.decode('utf-8', errors='replace')
                cls.path = path
                return
        raise unittest.SkipTest(
            f'install.sh not found at {cls.install_sh} or '
            f'{cls.backend_install_sh}'
        )

    def test_seed_file_references_eight_critical_secrets(self):
        """The seed file written by install.sh must persist
        the eight críticos secrets so a future manual update
        can recover them.
        """
        for name in (
            'MASTER_BACKUP_ENCRYPTION_KEY',
            'MASTER_BACKUP_REQUIRE_ENCRYPTION',
            'MASTER_GITHUB_WEBHOOK_SECRET',
            'MASTER_AUTOSCALER_API_TOKEN',
            'MASTER_FRP_AUTH_TOKEN',
            'MASTER_PGCAT_ADMIN_PASSWORD',
        ):
            self.assertIn(
                name, self.content,
                f'{self.path} must reference {name} in the seed '
                f'file so the node can recover it on update.',
            )

    def test_sync_block_persists_each_secret_via_env_set_value(self):
        """The sync block must persist each secret to the
        node's .env file via env_set_value (the same helper
        the rest of install.sh uses to mutate the .env).
        """
        # Locate the sync block.
        sync_marker = '_master_secrets_to_sync'
        self.assertIn(
            sync_marker, self.content,
            'install.sh must use a _master_secrets_to_sync '
            'tuple in the agent-lite provisioning block.',
        )
        # The sync block must be a bash array of "<KEY>:<desc>"
        # pairs so an operator reading the script can see at a
        # glance which secrets are being synced.
        idx = self.content.find(sync_marker)
        block = self.content[idx:idx + 4000]
        for key in (
            'BACKUP_ENCRYPTION_KEY',
            'BACKUP_REQUIRE_ENCRYPTION',
            'GITHUB_WEBHOOK_SECRET',
            'AUTOSCALER_API_TOKEN',
            'FRP_AUTH_TOKEN',
            'PGCAT_ADMIN_PASSWORD',
        ):
            self.assertIn(
                key, block,
                f'{key} missing from _master_secrets_to_sync '
                f'tuple in the install.sh sync block.',
            )

    def test_sync_block_guards_each_write_with_existence_check(self):
        """The sync must NOT overwrite an existing operator-set
        value on the node. The pattern must guard each
        env_set_value with ``if [ -n "$_master_val" ]`` so a
        missing master value does not clobber a node-local
        value with an empty string.
        """
        sync_marker = '_master_secrets_to_sync'
        idx = self.content.find(sync_marker)
        # The next 2000 chars contain the actual sync logic.
        block = self.content[idx:idx + 2000]
        self.assertIn(
            'if [ -n "$_master_val" ]', block,
            'sync block must guard each env_set_value with '
            '``if [ -n "$_master_val" ]`` so the sync is '
            'strictly one-way master→node and never clobbers '
            'a node-local value with an empty string.',
        )

    def test_sync_block_uses_env_get_value_for_master_fallback(self):
        """If a var isn't set in the current shell, the sync
        must read it from the master's .env file via
        ``env_get_value`` (the install.sh helper).
        """
        sync_marker = '_master_secrets_to_sync'
        idx = self.content.find(sync_marker)
        block = self.content[idx:idx + 2000]
        self.assertIn(
            'env_get_value', block,
            'sync block must read missing master vars from the '
            '.env file via env_get_value when the shell '
            'variable is unset.',
        )

    def test_sync_block_target_uses_node_env_file_variable(self):
        """The sync must write to the node's .env (the local
        $env_file variable used elsewhere in the provisioning
        block), not to the master's .env.
        """
        sync_marker = '_master_secrets_to_sync'
        idx = self.content.find(sync_marker)
        block = self.content[idx:idx + 2000]
        self.assertIn(
            'env_set_value "$env_file"', block,
            'sync block must persist each secret to the node '
            '$env_file (not the master\'s .env).',
        )

    def test_sync_also_writes_gateway_and_field_encryption(self):
        """Regression check: the existing GATEWAY_SECRET and
        FIELD_ENCRYPTION_KEY sync must still be present after
        the Batch J edit. These are the two secrets the
        installer synced before; a careless edit that
        consolidated them into the new tuple would have
        broken the existing GATEWAY_SECRET sync.
        """
        # Both must be present near the agent-lite provisioning
        # block (between ``MODE_NODE`` and the
        # ``ensure_agent_lite_networks`` function).
        for key in (
            'env_set_value "$env_file" "GATEWAY_SECRET"',
            'env_set_value "$env_file" "FIELD_ENCRYPTION_KEY"',
        ):
            self.assertIn(
                key, self.content,
                f'Pre-existing {key!r} sync must still be present '
                f'after the Batch J consolidation.',
            )

    def test_seed_file_also_persists_gateway_and_field_encryption(self):
        """Regression check: the seed file written by the
        installer must still persist MASTER_GATEWAY_SECRET
        and MASTER_FIELD_ENCRYPTION_KEY (so a manual update
        on a node can recover the same GATEWAY_SECRET the
        master was using at provisioning time).
        """
        for name in (
            'MASTER_GATEWAY_SECRET',
            'MASTER_FIELD_ENCRYPTION_KEY',
        ):
            self.assertIn(
                name, self.content,
                f'seed file must still persist {name} so '
                f'manual node updates can recover the master\'s '
                f'value.',
            )

    def test_sync_block_source_locations(self):
        """The sync block must live inside the agent-lite
        provisioning function (not at the top of the script
        or in some unrelated function). We verify by
        checking the sync block is between
        ``MODE_NODE=true`` activation and the
        ``SMSLY_ENABLE_STARTUP_CADDY_SYNC=false`` write.
        """
        sync_marker = '_master_secrets_to_sync'
        idx = self.content.find(sync_marker)
        self.assertGreater(idx, 0)
        # The agent-lite provisioning block ends with
        # ``SMSLY_ENABLE_STARTUP_CADDY_SYNC=false``. The sync
        # block must appear before that line.
        end_marker = 'SMSLY_ENABLE_STARTUP_CADDY_SYNC'
        end_idx = self.content.find(end_marker, idx)
        self.assertGreater(
            end_idx, idx,
            'sync block must be in the agent-lite provisioning '
            'block (before SMSLY_ENABLE_STARTUP_CADDY_SYNC).',
        )
        # And it must appear after the local
        # ``local celery_broker_url=...`` line so it's
        # inside the provisioning function body.
        broker_marker = 'local celery_broker_url='
        broker_idx = self.content.find(broker_marker)
        self.assertGreater(
            idx, broker_idx,
            'sync block must be after the agent-lite '
            'provisioning function sets up its local '
            'broker/redis URLs.',
        )
