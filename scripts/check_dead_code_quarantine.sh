#!/usr/bin/env bash
# scripts/check_dead_code_quarantine.sh
#
# Verify that nothing in production code imports from archive/ and that the
# original dead-code stubs have not been silently restored. Runs as part of CI
# so a future refactor cannot accidentally re-introduce these stubs without
# re-integrating them with the installer, compose files, INSTALLED_APPS, etc.
#
# See archive/DEAD_CODE_QUARANTINE.md for the full list and rationale.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail=0

# 1. No production code may import from archive/.
#    Exclude caches, build artifacts, virtualenvs, and the noise directories
#    that ship inside the repo (frontend/node_modules, backend/.venv, etc.)
#    so the check finishes in seconds rather than minutes.
if grep -rn \
    --exclude-dir=node_modules \
    --exclude-dir=.next \
    --exclude-dir=out \
    --exclude-dir=__pycache__ \
    --exclude-dir=.pytest_cache \
    --exclude-dir=.mypy_cache \
    --exclude-dir=.ruff_cache \
    --exclude-dir=htmlcov \
    --exclude-dir=target \
    --exclude-dir=dist \
    --exclude-dir=build \
    --exclude-dir=.eggs \
    --exclude-dir=.git \
    --exclude-dir=.venv \
    --exclude-dir=venv \
    --exclude-dir=site-packages \
    --exclude-dir=Lib \
    --exclude-dir=Include \
    --exclude-dir=Scripts \
    --exclude-dir=locale \
    --exclude='*.min.js' \
    --exclude='*.min.css' \
    --exclude='*.egg-info' \
    -E 'from archive|import archive' \
    backend/ frontend/ cli/ charts/ docker-compose.yml docker-compose.prod.yml install.sh ; then
    echo "ERROR: production code imports from archive/" >&2
    fail=1
fi

# 2. The original dead-code stub directories must not have been re-created
#    at the repo root without re-integration.
for stub in custom-addons rust_twin console; do
    if [ -d "$stub" ]; then
        echo "ERROR: dead-code stub '$stub/' has been restored without integration" >&2
        fail=1
    fi
done

# 3. The legacy Click-based CLI must not have been restored at cli/smsly.py.
if [ -f cli/smsly.py ]; then
    echo "ERROR: legacy Click-based cli/smsly.py has been restored; it was superseded by cli/smsly_cli.py" >&2
    fail=1
fi

# 4. archive/DEAD_CODE_QUARANTINE.md must exist and contain the expected
#    marker so a refactor cannot accidentally delete the manifest.
if [ ! -f archive/DEAD_CODE_QUARANTINE.md ] || ! grep -q "DEAD CODE QUARANTINE" archive/DEAD_CODE_QUARANTINE.md; then
    echo "ERROR: archive/DEAD_CODE_QUARANTINE.md is missing or has been altered" >&2
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    exit 1
fi

echo "Dead code quarantine OK"
