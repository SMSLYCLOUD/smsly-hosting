from pathlib import Path

from django.test import SimpleTestCase


class Finding121MiddlewareBuildAssertionTests(SimpleTestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[4]
        self.middleware_path = repo_root / "frontend" / "src" / "middleware.ts"

    def test_middleware_file_exists(self):
        self.assertTrue(
            self.middleware_path.exists(),
            f"middleware.ts not found at {self.middleware_path}",
        )

    def test_module_level_production_guard_throws(self):
        source = self.middleware_path.read_text(encoding="utf-8")
        self.assertIn('process.env.NODE_ENV === "production"', source)
        self.assertIn('throw new Error', source)
        self.assertIn('DEV_SHORT_CIRCUIT_ENABLED', source)

    def test_guard_runs_at_module_load_not_inside_handler(self):
        source = self.middleware_path.read_text(encoding="utf-8")
        guard_idx = source.find('process.env.NODE_ENV === "production"')
        middleware_fn_idx = source.find("export async function middleware")
        self.assertGreater(guard_idx, 0)
        self.assertGreater(middleware_fn_idx, 0)
        self.assertLess(
            guard_idx,
            middleware_fn_idx,
            "Production guard must be at module top level so it runs at "
            "build/import time, not on each request.",
        )

    def test_dev_short_circuit_remains_gated_by_flag(self):
        source = self.middleware_path.read_text(encoding="utf-8")
        self.assertIn(
            'process.env.NODE_ENV === "development" && DEV_SHORT_CIRCUIT_ENABLED',
            source,
        )

    def test_flag_constant_declared_at_module_top(self):
        source = self.middleware_path.read_text(encoding="utf-8")
        self.assertIn("const DEV_SHORT_CIRCUIT_ENABLED", source)
