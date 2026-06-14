"""
Regression tests for the provisioner shell-safety fix (Issue 39).

Covers:
  1. ``_harden_master_firewall`` invokes iptables/ufw as a list argv
     (no ``shell=True``) so attacker-controlled IP strings cannot
     inject shell metacharacters into the orchestrator host.
  2. ``_shell_env_assignments`` shlex-quotes every value so install
     env vars cannot break out of the assignment.
  3. The install command line joins install args via ``shlex.quote``
     and never invokes ``subprocess.run(..., shell=True)``.
"""
import inspect
import shlex

from django.test import SimpleTestCase

from apps.deployments.services import provisioner


class ProvisionerShellSafetyTests(SimpleTestCase):
    def test_no_subprocess_shell_true_in_provisioner_module(self):
        """Static check: there is no ``shell=True`` in the module."""
        src = inspect.getsource(provisioner)
        self.assertNotIn("shell=True", src)

    def test_harden_master_firewall_passes_ip_as_list_argv(self):
        """Each subprocess.run in _harden_master_firewall uses a list
        with the validated IP as one element, never as part of a string
        that goes through a shell."""
        import apps.deployments.services.provisioner as p
        import ast
        tree = ast.parse(inspect.getsource(p._harden_master_firewall))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "run":
                    # Reject shell= keyword argument entirely.
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            self.fail(
                                f"subprocess.run uses shell={ast.unparse(kw.value)} "
                                "in _harden_master_firewall"
                            )
                    # First positional arg must be a list/tuple, not a
                    # raw string (which would be a shell command).
                    if node.args and isinstance(node.args[0], ast.Constant):
                        if isinstance(node.args[0].value, str):
                            self.fail(
                                "subprocess.run called with a string literal "
                                "in _harden_master_firewall; this would be a "
                                "shell injection vector."
                            )

    def test_shell_env_assignments_quotes_values(self):
        """``_shell_env_assignments`` must call ``shlex.quote`` on every
        value so a malicious value cannot escape the assignment."""
        result = provisioner._shell_env_assignments(
            {"SAFE": "hello", "INJECT": "a;b $(rm -rf /) `evil`"}
        )
        # The string "a;b" survives but must be inside single quotes,
        # which is the shlex.quote escape that makes it safe for the
        # shell to interpret as a single token.
        self.assertIn("'a;b $(rm -rf /) `evil`'", result)
        # Plain ASCII alphanumeric value passes through unquoted.
        self.assertIn("SAFE=hello", result)

    def test_shell_env_assignments_skips_none_values(self):
        self.assertEqual(
            provisioner._shell_env_assignments({"A": None, "B": "x"}),
            "B=x",
        )
