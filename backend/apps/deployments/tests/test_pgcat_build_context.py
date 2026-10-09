"""PgCat build-context guard.

The pgcat image build broke silently when the Dockerfile referenced a file
removed from the build context (and the fix itself was never rebuilt
clean). This test statically asserts every ``COPY`` source in
``infrastructure/pgcat/Dockerfile.pgcat`` exists in the build context, so a
future removal/rename fails fast in CI instead of at deploy time. It does
not run ``docker build`` (needs a daemon); full rebuilds are verified
manually per the Dockerfile header notes.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

_COPY_RE = re.compile(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+\S+")


def _pgcat_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "infrastructure" / "pgcat" / "Dockerfile.pgcat"
        if candidate.exists():
            return candidate.parent
    raise AssertionError("infrastructure/pgcat/Dockerfile.pgcat not found")


class PgcatBuildContextTests(SimpleTestCase):
    def test_dockerfile_copy_sources_exist(self):
        pgcat_dir = _pgcat_dir()
        dockerfile = (pgcat_dir / "Dockerfile.pgcat").read_text(encoding="utf-8")
        sources = [
            m.group(1)
            for line in dockerfile.splitlines()
            if (m := _COPY_RE.match(line.strip())) and "--from=" not in line
        ]
        self.assertTrue(sources, "expected at least one COPY source")
        missing = [s for s in sources if not (pgcat_dir / s).exists()]
        self.assertEqual(missing, [])

    def test_entrypoint_and_render_script_present(self):
        pgcat_dir = _pgcat_dir()
        for name in ("entrypoint.sh", "render_pgcat_config.py", "pgcat.toml"):
            self.assertTrue((pgcat_dir / name).is_file(), name)
