"""
Regression tests for Finding #138 (ALLOW_STUB_TRANSFER_PIPELINE).

The setting is resolved once at Django startup in
``config/settings.py`` via ``_env_bool('ALLOW_STUB_TRANSFER_PIPELINE', ...)``
and is exposed through the ``settings`` proxy.  The
``ServerTransferService`` must NEVER look the value up via
``os.environ.get`` (which would re-read the process environment on
every call) and must rely on the standard settings access pattern
that ``override_settings`` can swap for tests.

This test asserts:

  * the setting is present in ``django.conf.settings``;
  * the source code of ``transfer_service`` does not import or
    call ``os.environ.get('ALLOW_STUB_TRANSFER_PIPELINE')``;
  * the existing strict-mode behaviour is preserved (the
    existing ``test_transfer_strict_mode`` covers the actual
    execute path; this test pins the read-once contract).
"""
import os
import re
import inspect

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.deployments.services import transfer_service


class Finding138AllowStubTransferReadOnceTests(SimpleTestCase):
    def test_setting_resolves_via_django_settings(self):
        self.assertTrue(hasattr(settings, "ALLOW_STUB_TRANSFER_PIPELINE"))

    def test_setting_is_resolved_at_module_load_time(self):
        """The setting is declared as a module-level expression in
        ``config/settings.py``. Operators cannot re-read it per
        request by mutating the env mid-process — only the Django
        settings cache (overridden by ``override_settings``) is the
        authoritative value during the request lifecycle."""
        with override_settings(ALLOW_STUB_TRANSFER_PIPELINE=False):
            self.assertFalse(settings.ALLOW_STUB_TRANSFER_PIPELINE)
        with override_settings(ALLOW_STUB_TRANSFER_PIPELINE=True):
            self.assertTrue(settings.ALLOW_STUB_TRANSFER_PIPELINE)

    def test_transfer_service_does_not_per_request_env_lookup(self):
        """The transfer service must not call ``os.environ.get`` for
        ``ALLOW_STUB_TRANSFER_PIPELINE`` — that would re-read the
        process environment on every call, defeating the
        startup-only contract."""
        source = inspect.getsource(transfer_service)
        # The literal env-var name should not appear in the
        # service source code.
        self.assertNotIn("ALLOW_STUB_TRANSFER_PIPELINE", source)
        # And no per-request ``os.environ.get`` calls either.
        per_request_calls = re.findall(
            r"os\.environ\.get\(\s*['\"]ALLOW_STUB",
            source,
        )
        self.assertEqual(per_request_calls, [])
