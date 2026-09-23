"""Run-coalescing decorator (Redis assist layer).

No DB, no broker: exercises skip_if_recent against the test LocMem
cache plus decorator transparency (name/module preservation for
Celery task naming, arg forwarding incl. bind-style self).
"""
from django.core.cache import cache
from django.test import SimpleTestCase

from apps.core.tasks.coalesce import skip_if_recent


def _tracked_fn(*args, **kwargs):
    _tracked_fn.calls.append((args, kwargs))
    return {"status": "ok"}


_tracked_fn.calls = []


class SkipIfRecentTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        _tracked_fn.calls = []

    def test_first_run_executes(self):
        decorated = skip_if_recent("coalesce:test-first", 60)(_tracked_fn)
        result = decorated()
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(len(_tracked_fn.calls), 1)

    def test_second_run_within_ttl_skips(self):
        decorated = skip_if_recent("coalesce:test-skip", 60)(_tracked_fn)
        decorated()
        result = decorated()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "coalesced")
        self.assertEqual(len(_tracked_fn.calls), 1)

    def test_run_after_expiry_executes(self):
        decorated = skip_if_recent("coalesce:test-expiry", 60)(_tracked_fn)
        decorated()
        cache.delete("coalesce:test-expiry")
        result = decorated()
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(len(_tracked_fn.calls), 2)

    def test_forwards_args_and_preserves_name(self):
        def raw(self, x, flag=False):
            return (self, x, flag)

        decorated = skip_if_recent("coalesce:test-args", 60)(raw)
        sentinel = object()
        outcome = decorated(sentinel, 1, flag=True)
        self.assertIs(outcome[0], sentinel)
        self.assertEqual(outcome[1:], (1, True))
        self.assertEqual(decorated.__name__, "raw")
        self.assertIn("coalesce", decorated.__module__)

    def test_distinct_keys_are_independent(self):
        first = skip_if_recent("coalesce:test-a", 60)(_tracked_fn)
        second = skip_if_recent("coalesce:test-b", 60)(_tracked_fn)
        first()
        self.assertEqual(second(), {"status": "ok"})
        self.assertEqual(len(_tracked_fn.calls), 2)
