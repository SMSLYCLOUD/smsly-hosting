"""Run the local multi-server harness tests."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    env.setdefault("PYTHONPATH", str(ROOT / "backend"))
    env.setdefault("TESTING", "1")

    command = [
        sys.executable,
        "-m",
        "pytest",
        "backend/apps/deployments/tests/test_multi_server_local_harness.py",
        "-q",
    ]
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
