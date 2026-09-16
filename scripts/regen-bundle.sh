#!/bin/bash
# scripts/regen-bundle.sh — regenerate backend/install.sh inlined lib blocks.
#
# The standalone installer (backend/install.sh) carries every lib/*.sh file
# inline behind `# --- lib/X.sh ---` markers (nested for parents like
# harden.sh/common.sh/platform.sh/ops.sh/fresh.sh, which source children).
# There is no magic here: this script rebuilds each marked block from the
# live lib/ tree so the bundle can never drift behind again.
#
#   sudo bash scripts/regen-bundle.sh          # rewrite bundle in place
#   sudo bash scripts/regen-bundle.sh --check  # CI drift gate (exit 1 if stale)
#
# Rules:
#   - Child order follows the live parent's `source ... lib/Y.sh` / `. ...`
#     lines, never a hardcoded list — new submodules (cf. the 2026-09
#     harden_openappsec drift, where the bundle silently skipped the whole
#     WAF) are picked up automatically.
#   - Files the live tree loads from disk at runtime (lib/install-gvisor.sh,
#     lib/install-kata.sh — invoked via `bash $install_dir/lib/...` with
#     `-f` guards) are intentionally NOT inlined.
#   - AGENTS.md checklist: `bash -n` every changed .sh file after regen.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="$REPO_ROOT/backend/install.sh"
LIB_DIR="$REPO_ROOT/lib"
CHECK_MODE=false
[ "${1:-}" = "--check" ] && CHECK_MODE=true

if [ ! -f "$BUNDLE" ]; then
    echo "ERROR: bundle not found: $BUNDLE" >&2
    exit 1
fi

python3 - "$BUNDLE" "$LIB_DIR" "$([ "$CHECK_MODE" = true ] && echo --check || echo --write)" <<'PY'
import re
import sys

bundle_path, lib_dir, mode = sys.argv[1], sys.argv[2], sys.argv[3]
check_only = (mode == "--check")

with open(bundle_path, "rb") as fh:
    raw = fh.read()
lines = raw.split(b"\n")

# lib files invoked via disk path at runtime (with -f guards) — never inline.
SKIP_INLINE = {"install-gvisor.sh", "install-kata.sh"}

# Match `source .../Y.sh`, `source ${...}/Y.sh`, `. "$DIR/Y.sh"` forms.
# The path may contain quotes/parens (e.g. $(dirname "${BASH_SOURCE[0]}")),
# so match lazily up to the first .sh basename, then verify the basename
# exists under lib/ (and is not skipped).
SRC_RE = re.compile(rb'''(?:source|\.)\s+.*?([A-Za-z0-9_][A-Za-z0-9_.\-]*\.sh)''')

live_cache = {}

def live_lines(name):
    if name not in live_cache:
        with open(f"{lib_dir}/{name}", "rb") as fh:
            content = fh.read().split(b"\n")
        if content and content[-1] == b"":
            content = content[:-1]
        live_cache[name] = content
    return live_cache[name]

def sourced_children(name):
    kids = []
    for line in live_lines(name):
        for m in SRC_RE.finditer(line):
            base = m.group(1).decode()
            if base in SKIP_INLINE:
                continue
            try:
                with open(f"{lib_dir}/{base}", "rb"):
                    pass
            except OSError:
                continue
            # Only treat it as an inlined child when the bundle knows it:
            # leaf files sourced for shared helpers (logging.sh etc. are
            # covered via their parents) — every current parent/child pair
            # below is verified against the live tree.
            kids.append(base)
    # De-dupe preserving order.
    seen, ordered = set(), []
    for k in kids:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered

def build(name, depth=0):
    assert depth < 8, f"nesting too deep at {name}"
    out = []
    # Track double-quote parity across lines: a `source` line inside a
    # double-quoted string (e.g. bash -c "...") must NEVER be inlined —
    # the inlined content carries its own quotes and breaks bundle
    # syntax (2026-09-16: env.sh inlined inside bash -c broke `bash -n`).
    # Fail loudly instead of emitting a broken installer.
    in_dq = False
    for line in live_lines(name):
        kids = []
        for m in SRC_RE.finditer(line):
            base = m.group(1).decode()
            if base in SKIP_INLINE:
                continue
            try:
                with open(f"{lib_dir}/{base}", "rb"):
                    pass
                kids.append(base)
            except OSError:
                continue
        if kids and line.strip().startswith((b"source", b". ")):
            assert not in_dq, (
                f"{name}: source line inside double quotes would break "
                f"the bundle (use a quoted heredoc instead): {line!r}"
            )
            # One source line inlines exactly one lib file in this tree.
            assert len(kids) == 1, f"{name}: ambiguous source line: {line!r}"
            kid = kids[0]
            out.append(f"# --- lib/{kid} ---".encode())
            out.extend(build(kid, depth + 1))
            out.append(f"# --- end lib/{kid} ---".encode())
        else:
            out.append(line)
        # Update quote state from this line (strip `#` comments only at
        # even parity — a `#` inside quotes is literal).
        code = line
        if not in_dq:
            hash_at = code.find(b"#")
            if hash_at != -1 and code[:hash_at].count(b'"') % 2 == 0:
                code = code[:hash_at]
        code = code.replace(b'\\"', b"")
        if code.count(b'"') % 2 == 1:
            in_dq = not in_dq
    return out

# Top-level blocks present in the bundle (depth 0).
top_blocks = []
for i, line in enumerate(lines):
    m = re.match(rb"^# --- (lib/\S+\.sh) ---$", line.strip())
    if m:
        # depth 0 = not inside another lib block; verified by stack walk below.
        top_blocks.append((m.group(1).decode(), i))

# Stack-walk to keep only depth-0 markers.
depth, tops = 0, []
for name, idx in top_blocks:
    # Recompute depth by scanning markers up to idx.
    d = 0
    for j in range(idx):
        s = lines[j].strip()
        if re.match(rb"^# --- lib/\S+\.sh ---$", s):
            d += 1
        elif re.match(rb"^# --- end lib/\S+\.sh ---$", s):
            d -= 1
    if d == 0:
        tops.append((name, idx))

# Find each top block's end marker.
regions = []
for name, start in tops:
    want_end = f"# --- end {name} ---".encode()
    j = start + 1
    d = 1
    while j < len(lines):
        s = lines[j].strip()
        if re.match(rb"^# --- lib/\S+\.sh ---$", s):
            d += 1
        elif re.match(rb"^# --- end lib/\S+\.sh ---$", s):
            d -= 1
            if d == 0:
                assert s == want_end, f"{name}: mismatched end {s!r}"
                break
        j += 1
    assert j < len(lines), f"no end marker for {name}"
    regions.append((name, start, j))

changed = []
out = list(lines)
# Replace back-to-front so indices stay valid.
for name, start, end in reversed(regions):
    base = name.split("/", 1)[1]
    fresh_inner = build(base)
    if out[start + 1:end] != fresh_inner:
        changed.append(name)
        out[start + 1:end] = fresh_inner

if check_only:
    if changed:
        print("STALE bundle blocks (run scripts/regen-bundle.sh):")
        for name in sorted(set(changed)):
            print(f"  {name}")
        sys.exit(1)
    print(f"bundle in sync ({len(regions)} blocks)")
else:
    if not changed:
        print(f"bundle already in sync ({len(regions)} blocks)")
    else:
        with open(bundle_path, "wb") as fh:
            fh.write(b"\n".join(out))
        print(f"regenerated {len(set(changed))} stale block(s) in {len(regions)} total:")
        for name in sorted(set(changed)):
            print(f"  {name}")
PY
