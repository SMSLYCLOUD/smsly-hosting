#!/bin/bash
# Grid by SMSLY - Fresh Install Mode Module
# Sourced by install.sh for fresh installs

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

export PATH="/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# When running from the repo layout, fresh.sh lives in lib/ next to state.sh.
# When running from the regenerated self-contained installer (backend/install.sh)
# the state.sh content is inlined at this exact line by the regen pipeline, so
# the file itself will not exist. The source line must stay in its canonical
# form (no redirects) for the inliner to recognize and replace it.
. "$SCRIPT_DIR/state.sh"

if ! command -v is_checkpoint_done >/dev/null 2>&1; then
    echo "ERROR: is_checkpoint_done not defined after sourcing state.sh" >&2
    exit 1
fi

source "$SCRIPT_DIR/fresh_interactive.sh"
source "$SCRIPT_DIR/fresh_preflight.sh"
source "$SCRIPT_DIR/fresh_deps.sh"
source "$SCRIPT_DIR/fresh_config.sh"
source "$SCRIPT_DIR/fresh_deploy.sh"
source "$SCRIPT_DIR/fresh_database.sh"
source "$SCRIPT_DIR/fresh_admin.sh"
source "$SCRIPT_DIR/fresh_caddy.sh"
source "$SCRIPT_DIR/fresh_hardening.sh"
source "$SCRIPT_DIR/fresh_verify.sh"

return 0
