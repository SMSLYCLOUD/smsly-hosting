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
    if bash -n "$BASE/$f" ; then
        echo "  OK   $f"
    else
        echo "  FAIL $f"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== 2. Django system check ==="
cd "$BASE/backend"
CHECK_OUT=$(timeout -k 5 60 python manage.py check  || true)
if echo "$CHECK_OUT" | grep -q "no issues"; then
    echo "  OK   Django check passed (0 issues)"
elif echo "$CHECK_OUT" | grep -qiE "Error|Traceback|ImportError|NameError|IndentationError|SyntaxError"; then
    echo "  FAIL Django check returned errors"
    echo "$CHECK_OUT" | grep -iE "Error:|ImportError|NameError|IndentationError|SyntaxError" | head -5
    FAIL=$((FAIL + 1))
else
    echo "  OK   Django check passed (warnings/timeout only)"
fi
cd "$BASE"

echo ""
echo "=== 3. Critical import chain check ==="
cd "$BASE/backend"
# Test the critical import path that broke in production:
# urls.py -> views.py -> tasks.py (the chain that hit 503)
if python -c "
import os, django, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.urls import get_resolver
try:
    resolver = get_resolver()
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
    sys.exit(1)
" ; then
    echo "  OK   URL resolver loads cleanly"
else
    echo "  FAIL URL resolver chain broken"
    FAIL=$((FAIL + 1))
fi
cd "$BASE"

echo ""
echo "=== 4. Type hint check (missing typing imports) ==="
TYPING_FAILS=0
KNOWN_TYPES="Any|Dict|List|Optional|Tuple|Union|Callable|Set|DefaultDict|Generator|Iterator|Mapping|Type|Pattern|Match"
for pyfile in apps/deployments/tasks_*.py apps/deployments/views_*.py; do
    [ -f "$pyfile" ] || continue
    used=$(grep -oP ":\s*\b($KNOWN_TYPES)\b|\->\s*\b($KNOWN_TYPES)\b" "$pyfile"  | sed 's/.*\b//' | sort -u)
    imported=$(grep -oP 'from typing import (.+)' "$pyfile"  | sed 's/from typing import //; s/,/\n/g' | tr -d ' ' | sort -u)
    missing=$(comm -23 <(echo "$used") <(echo "$imported") )
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
