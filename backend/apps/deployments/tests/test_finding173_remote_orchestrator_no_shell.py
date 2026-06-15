# pylint: disable=invalid-name
"""Tests for the ``RemoteOrchestrator.sync_env_vars`` path
(Issue 173).

The audit flagged lines 1170-1200 of ``remote_orchestrator.py`` as
a potential ``subprocess.run(shell=True)`` vector. The current
implementation in that range is ``sync_env_vars`` which makes
HTTPS requests — it has no subprocess calls at all.  The tests
below pin that contract: any future refactor that re-introduces
``shell=True`` or a string-form ``subprocess.run`` in the
sync_env_vars path is rejected.
"""
import inspect

from django.test import SimpleTestCase

from apps.deployments.services import remote_orchestrator
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


class Finding173RemoteOrchestratorNoShellTests(SimpleTestCase):
    def test_no_subprocess_at_all_in_module(self):
        module_source = inspect.getsource(remote_orchestrator)
        self.assertNotIn("subprocess", module_source)
        self.assertNotIn("shell=True", module_source)
        self.assertNotIn("os.system", module_source)

    def test_sync_env_vars_method_uses_safe_string_formatting(self):
        source = inspect.getsource(RemoteOrchestrator.sync_env_vars)
        self.assertIn("safe_vars.append", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.popen", source)
        self.assertNotIn("os.system", source)
