#!/bin/bash
# =============================================================================
# Pre-push validation — catches syntax errors BEFORE they reach production.
# Usage: bash scripts/pre-push-check.sh
# =============================================================================
set -euo pipefail
FAIL=0
BASE="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== 1. Shell syntax check (bash -n) ==="
for f in install.sh lib/*.sh scripts/*.sh; do
    [ -f "$BASE/$f" ] || continue
    if bash -n "$BASE/$f" 2>/dev/null; then
        echo "  OK   $f"
    else
        echo "  FAIL $f"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== 2. Django system check ==="
if python "$BASE/backend/manage.py" check 2>/dev/null; then
    echo "  OK   Django check passed"
else
    echo "  FAIL Django check"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== 3. Python import check (catches circular imports, missing modules) ==="
cd "$BASE/backend"
PY_FILES=$(find apps -name "*.py" -not -path "*/migrations/*" -not -name "__init__.py" 2>/dev/null)
IMPORT_FAILS=0
for pyfile in $PY_FILES; do
    mod=$(echo "$pyfile" | sed 's|/|.|g' | sed 's|\.py$||')
    if python -c "import $mod" 2>/dev/null; then
        :
    else
        IMPORT_FAILS=$((IMPORT_FAILS + 1))
    fi
done
if [ "$IMPORT_FAILS" -eq 0 ]; then
    echo "  OK   All modules import cleanly"
else
    echo "  FAIL $IMPORT_FAILS module(s) failed to import"
    FAIL=$((FAIL + IMPORT_FAILS))
fi

echo ""
echo "=== 4. Type hint check (missing typing imports) ==="
TYPING_FAILS=0
KNOWN_TYPES="Any|Dict|List|Optional|Tuple|Union|Callable|Set|DefaultDict|Generator|Iterator|Mapping|Type|Pattern|Match"
for pyfile in apps/deployments/tasks_*.py apps/deployments/views_*.py; do
    [ -f "$pyfile" ] || continue
    used=$(grep -oP ":\s*\b($KNOWN_TYPES)\b|\->\s*\b($KNOWN_TYPES)\b" "$pyfile" 2>/dev/null | sed 's/.*\b//' | sort -u)
    imported=$(grep -oP 'from typing import (.+)' "$pyfile" 2>/dev/null | sed 's/from typing import //; s/,/\n/g' | tr -d ' ' | sort -u)
    missing=$(comm -23 <(echo "$used") <(echo "$imported") 2>/dev/null)
    if [ -n "$missing" ]; then
        echo "  FAIL $pyfile: missing typing imports: $(echo $missing | tr '\n' ' ')"
        TYPING_FAILS=$((TYPING_FAILS + 1))
    fi
done
if [ "$TYPING_FAILS" -eq 0 ]; then
    echo "  OK   All type hints have imports"
else
    echo "  FAIL $TYPING_FAILS file(s) with missing typing imports"
    FAIL=$((FAIL + TYPING_FAILS))
fi

echo ""
echo "=== 5. Circular import detection ==="
# Check for cross-imports that could cause partial initialization errors
CIRC_FAILS=0
for pyfile in apps/deployments/tasks_*.py apps/deployments/views_*.py; do
    [ -f "$pyfile" ] || continue
    self_mod=$(basename "$pyfile" .py)
    # Check for self-imports
    if grep -q "from .$self_mod import" "$pyfile"; then
        echo "  FAIL $pyfile: imports from itself (self-import)"
        CIRC_FAILS=$((CIRC_FAILS + 1))
    fi
done
if [ "$CIRC_FAILS" -eq 0 ]; then
    echo "  OK   No self-imports detected"
else
    echo "  FAIL $CIRC_FAILS file(s) with self-imports"
    FAIL=$((FAIL + CIRC_FAILS))
fi

echo ""
echo "============================================================"
if [ "$FAIL" -eq 0 ]; then
    echo "  ALL CHECKS PASSED — safe to push"
else
    echo "  $FAIL CHECK(S) FAILED — fix before pushing"
fi
echo "============================================================"
exit $FAIL
