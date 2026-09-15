"""Unit tests for BuildMixin._drain_build_log_tail (no DB, no Docker).

Regression coverage for the 2026-09-15 silent build death: the SDK
abandons its output generator when it raises, so failures presented as
a bare "Docker build failed" with a clean-looking log (170 clean lines,
zero error text). The drain recovers the daemon-side reason boundedly.
"""
from django.test import SimpleTestCase

from apps.deployments.services.pipeline.build import (
    BUILD_FAIL_TAIL_CHARS,
    BUILD_FAIL_TAIL_LINES,
    BuildMixin,
)


def _mixin(secrets=None):
    mixin = BuildMixin()
    mixin.secret_values = secrets or []
    return mixin


def _stream(*entries):
    for entry in entries:
        yield entry


class DrainBuildLogTailTests(SimpleTestCase):
    def test_none_generator_returns_empty(self):
        self.assertEqual(_mixin()._drain_build_log_tail(None), "")

    def test_error_entry_captured_with_marker(self):
        gen = _stream(
            {"stream": "Step 12/20 : RUN pip install\\n"},
            {"errorDetail": {"message": "executor failed: OOMKilled"}},
        )
        tail = _mixin()._drain_build_log_tail(gen)
        self.assertIn("[docker daemon build-output tail]", tail)
        self.assertIn("OOMKilled", tail)
        self.assertIn("pip install", tail)

    def test_plain_error_key_captured(self):
        gen = _stream({"error": "failed to solve: no space left on device"})
        tail = _mixin()._drain_build_log_tail(gen)
        self.assertIn("no space left", tail)

    def test_generator_blowup_still_returns_captured(self):
        def bad():
            yield {"stream": "Step 1/2\\n"}
            raise RuntimeError("connection reset")
        tail = _mixin()._drain_build_log_tail(bad())
        self.assertIn("Step 1/2", tail)
        self.assertIn("stream ended", tail)

    def test_non_dict_entries_skipped(self):
        tail = _mixin()._drain_build_log_tail(_stream("junk", None, 42))
        self.assertEqual(tail, "")

    def test_secrets_redacted(self):
        gen = _stream({"stream": "ARG MY_TOKEN=s3cr3tvalue-abc\\n"})
        tail = _mixin(secrets=["s3cr3tvalue-abc"])._drain_build_log_tail(gen)
        self.assertNotIn("s3cr3tvalue-abc", tail)
        self.assertIn("***", tail)

    def test_tail_bounded(self):
        gen = _stream(*[{"stream": f"line {i}\\n"} for i in range(400)])
        tail = _mixin()._drain_build_log_tail(gen)
        self.assertIn("line 399", tail)
        self.assertNotIn("line 0\n", tail)
        self.assertLessEqual(len(tail), BUILD_FAIL_TAIL_CHARS + 200)
        self.assertEqual(BUILD_FAIL_TAIL_LINES, 60)
