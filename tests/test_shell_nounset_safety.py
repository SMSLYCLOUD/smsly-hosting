"""Guard against the `local x y` + `set -u` silent-death class.

Root cause (2026-09-12 fresh-install abort with zero output): in bash,
`local auth user pass` DECLARES but does NOT assign — the variables stay
UNSET, and any read under `set -u`/`set -euo pipefail` is instantly fatal
in a way no `||` guard or `if` condition can catch (nounset aborts even
"guarded" contexts). The installer died right after the Falco bootstrap
with no error because the stderr redirect on the caller hid bash's
"unbound variable" message too.

Rule enforced here: multi-name `local` declarations in installer shell
code must initialize every name (`local x="" y=""`). Those are the lines
where one name most often ends up conditionally assigned while another
is read — the exact shape of the 2026-09-12 abort. (Single bare
`local x` is safe when assigned before first read; left to review.)
"""

import os
import re
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("lib", "scripts")
SCAN_TOP = ("install.sh",)
# Mode-entry files contain top-level flow code; still scan them — the
# rule applies everywhere `set -u` is in effect.
BARE_LOCAL_RE = re.compile(r"^[ \t]*local\s+([A-Za-z_][A-Za-z0-9_]*(\s+[A-Za-z_][A-Za-z0-9_]*)+)[ \t]*$")


def _shell_files():
    files = []
    for top in SCAN_TOP:
        path = os.path.join(REPO_ROOT, top)
        if os.path.isfile(path):
            files.append(path)
    for dirname in SCAN_DIRS:
        d = os.path.join(REPO_ROOT, dirname)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".sh"):
                files.append(os.path.join(d, name))
    return files


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def _bare_local_vars(line):
    """Return var names from a bare multi-var `local a b` line.

    Only multi-name declarations are flagged: those are the ones where a
    human most often leaves one name conditionally assigned (the
    2026-09-12 installer abort). A single bare `local x` is safe as long
    as it is assigned before any read — verified by review, not by this
    test. Lines containing `=` are never flagged.
    """
    match = BARE_LOCAL_RE.match(line.split("#", 1)[0])
    if not match:
        return []
    names = match.group(1).split()
    if len(names) < 2 or any("=" in token for token in names):
        return []
    return names


class TestNounsetSafeLocals(unittest.TestCase):
    def test_no_bare_multi_var_locals(self):
        offenders = []
        for path in _shell_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    names = _bare_local_vars(line)
                    # Single `local x` followed by immediate assignment is
                    # safe; only multi-name declarations are enforced here.
                    if names:
                        offenders.append(f"{_rel(path)}:{lineno}: local {' '.join(names)}")
        self.assertEqual(
            offenders,
            [],
            "Bare multi-var `local` declarations (unset vars fatal under set -u). "
            "Initialize: local x=\"\" y=\"\"\n" + "\n".join(offenders),
        )

    def test_installer_scripts_parse(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")
        failures = []
        for path in _shell_files():
            # Forward-slash relative path so git-bash/WSL resolve it from
            # the repo root regardless of host path conventions.
            proc = subprocess.run(
                [bash, "-n", _rel(path)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=REPO_ROOT,
            )
            if proc.returncode != 0:
                failures.append(f"{_rel(path)}: {proc.stderr.strip()}")
        self.assertEqual(failures, [], "bash -n failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
