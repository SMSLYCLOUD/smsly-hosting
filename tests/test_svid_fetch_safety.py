"""Exercise SVID refresh with Docker replaced at the shell command boundary."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash not available")
class TestSvidFetchSafety(unittest.TestCase):
    def run_shell(self, body, *, names="", fail_shared="", inspect_failure=""):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "docker.log"
            env = os.environ.copy()
            env.update(SVID_TEST_LOG=log.as_posix(), SVID_TEST_NAMES=names,
                       SVID_TEST_FAIL_SHARED=fail_shared,
                       SVID_TEST_INSPECT_FAILURE=inspect_failure)
            script = r'''
set -eu
# Run commands immediately, recording which Docker calls have a deadline.
timeout() (
    if [ "${1:-}" = "-k" ]; then shift 2; fi
    SVID_TEST_DEADLINE="${1:-}"
    shift
    "$@"
)
docker() {
    printf '%s|%s\n' "${SVID_TEST_DEADLINE:-unbounded}" "$*" >> "$SVID_TEST_LOG"
    case "$1" in
        ps) printf '%s\n' "$SVID_TEST_NAMES" ;;
        inspect)
            [ -z "$SVID_TEST_INSPECT_FAILURE" ] || return 124
            printf '%s\n' '<no value>' ;;
        exec)
            case "$*" in
                *'sh -c cp -f'* )
                    case "$*" in
                        *"${SVID_TEST_FAIL_SHARED:-__none__}"*)
                            [ -z "$SVID_TEST_FAIL_SHARED" ] || return 124 ;;
                    esac ;;
            esac ;;
        *) return 99 ;;
    esac
}
. lib/svid_fetch.sh
''' + body
            proc = subprocess.run([BASH, "-c", script], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=15)
            calls = log.read_text().splitlines() if log.exists() else []
            return proc, calls

    def test_source_does_not_invoke_docker(self):
        proc, calls = self.run_shell("\n:")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls, [])

    def test_no_workloads_is_success_without_exec(self):
        proc, calls = self.run_shell("\nsvid_fetch_main")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to fetch", proc.stdout)
        self.assertTrue(calls)
        self.assertFalse(any("|exec " in call for call in calls), calls)
        self.assertFalse(any(call.startswith("unbounded|") for call in calls), calls)

    def test_gateway_is_discovered_and_all_docker_calls_are_bounded(self):
        proc, calls = self.run_shell(
            '\nSERVICES="smsly-backend"\nsvid_fetch_main',
            names="smsly-backend\nsmsly-security-gateway-blue\nsmsly-security-gateway-envoy")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("smsly-backend refreshed", proc.stdout)
        self.assertIn("smsly-security-gateway-blue refreshed", proc.stdout)
        self.assertNotIn("smsly-security-gateway-envoy refreshed", proc.stdout)
        self.assertFalse(any(call.startswith("unbounded|") for call in calls), calls)
        self.assertTrue(any("cp -f" in call for call in calls), calls)

    def test_inspect_timeout_falls_back_to_container_name(self):
        proc, calls = self.run_shell(
            '\nSERVICES="smsly-backend"\nsvid_fetch_main',
            names="smsly-backend", inspect_failure="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("smsly-backend refreshed (smsly-backend)", proc.stdout)
        self.assertFalse(any(call.startswith("unbounded|") for call in calls), calls)

    def test_shared_normalization_timeout_does_not_block_next_workload(self):
        proc, calls = self.run_shell(
            '\nSERVICES="smsly-backend smsly-email"\nsvid_fetch_main',
            names="smsly-backend\nsmsly-email", fail_shared="smsly-backend")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("smsly-backend shared-normalize FAILED", proc.stdout)
        self.assertIn("smsly-email refreshed", proc.stdout)
        self.assertFalse(any(call.startswith("unbounded|") for call in calls), calls)


if __name__ == "__main__":
    unittest.main()
