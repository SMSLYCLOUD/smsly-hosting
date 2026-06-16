# pylint: disable=invalid-name
import importlib
import inspect

from django.test import SimpleTestCase


class Finding102FactoryImportTests(SimpleTestCase):
    def test_factory_module_imports_cleanly(self):
        module = importlib.import_module("apps.cloud.services.factory")
        self.assertIsNotNone(module)
        self.assertTrue(hasattr(module, "get_cloud_adapter"))

    def test_factory_module_has_no_shell_or_insecure_calls(self):
        module = importlib.import_module("apps.cloud.services.factory")
        source = inspect.getsource(module)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("verify=False", source)
        self.assertNotIn("os.system(", source)
        self.assertNotIn("subprocess.", source)
        self.assertNotIn("eval(", source)
        self.assertNotIn("exec(", source)
