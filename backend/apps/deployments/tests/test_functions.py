import os
import shutil
import tempfile

from django.test import TestCase

from apps.cloud.services.function_provisioner import FunctionProvisioner


class MockService:
    def __init__(self, runtime, code):
        self.function_runtime = runtime
        self.function_code = code

class FunctionProvisionerTest(TestCase):
    def setUp(self):
        self.build_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.build_dir)

    def test_prepare_node_context(self):
        service = MockService(runtime="nodejs18", code="module.exports = (req, res) => res.send('ok');")
        FunctionProvisioner.prepare_context(service, self.build_dir)

        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "Dockerfile")))
        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "index.js")))
        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "server.js")))
        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "package.json")))

        with open(os.path.join(self.build_dir, "Dockerfile")) as f:
            content = f.read()
            self.assertIn("USER node", content) # Security check for non-root user
            self.assertNotIn("npm install", content)

        with open(os.path.join(self.build_dir, "server.js")) as f:
            content = f.read()
            self.assertIn("/health", content)
            self.assertNotIn("require('express')", content)

    def test_prepare_python_context(self):
        service = MockService(runtime="python3.9", code="def handler(event):\n    return 'ok'")
        FunctionProvisioner.prepare_context(service, self.build_dir)

        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "Dockerfile")))
        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "main.py")))
        self.assertTrue(os.path.exists(os.path.join(self.build_dir, "server.py")))
        with open(os.path.join(self.build_dir, "Dockerfile")) as f:
            content = f.read()
            self.assertIn("USER function_user", content) # Security check for non-root user
            self.assertNotIn("pip install", content)

        with open(os.path.join(self.build_dir, "server.py")) as f:
            content = f.read()
            self.assertIn("/health", content)
            self.assertIn("ThreadingHTTPServer", content)
