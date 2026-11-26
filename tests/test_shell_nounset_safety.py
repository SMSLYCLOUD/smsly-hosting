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
LOCAL_LINE_RE = re.compile(r"^[ \t]*local\s+(.+)$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _split_local_tokens(rest):
    """Split `local` arguments at top-level whitespace, ignoring comments/quotes/$()."""
    tokens = []
    buf = []
    in_single = False
    in_double = False
    in_backtick = False
    paren_depth = 0
    escaped = False
    i = 0
    n = len(rest)

    while i < n:
        ch = rest[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if in_single:
            if ch == "'":
                in_single = False
            buf.append(ch)
            i += 1
            continue
        if ch == "\\":
            escaped = True
            buf.append(ch)
            i += 1
            continue
        if ch == "`" and not in_double:
            in_backtick = not in_backtick
            buf.append(ch)
            i += 1
            continue
        if in_backtick:
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and paren_depth == 0:
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue
        if not in_double and paren_depth == 0 and ch == "(":
            paren_depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == "$" and i + 1 < n and rest[i + 1] == "(":
            paren_depth += 1
            buf.append("$(")
            i += 2
            continue
        if paren_depth > 0:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
            buf.append(ch)
            i += 1
            continue
        if not in_double:
            if ch in (";", "&", "|"):
                break
            if ch == "#" and not buf:
                break
            if ch in (" ", "\t"):
                if buf:
                    tokens.append("".join(buf))
                    buf = []
                i += 1
                continue
        buf.append(ch)
        i += 1

    if buf:
        tokens.append("".join(buf))
    return tokens


def _bare_local_vars(line):
    """Return uninitialized var names from a multi-var `local` declaration.

    Flags both fully bare (`local a b`) and mixed (`local a="" b c=""`)
    multi-name `local` lines: those are the ones where a variable most often
    ends up conditionally assigned and fatal under `set -u`. Single bare
    `local x` is left to review.
    """
    match = LOCAL_LINE_RE.match(line)
    if not match:
        return []
    tokens = _split_local_tokens(match.group(1))
    # Skip leading `local` options (e.g. -r, -i, -a, -A, -x) and `--`.
    idx = 0
    while idx < len(tokens) and tokens[idx].startswith("-"):
        tok = tokens[idx]
        idx += 1
        if tok == "--":
            break
    var_tokens = tokens[idx:]
    if len(var_tokens) < 2:
        return []
    bare = [tok for tok in var_tokens if IDENT_RE.match(tok)]
    return bare


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
