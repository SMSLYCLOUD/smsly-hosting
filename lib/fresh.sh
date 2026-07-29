#!/bin/bash
# Grid by SMSLY - Fresh Install Mode Module
# Sourced by install.sh for fresh installs

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

export PATH="/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source state.sh with explicit error handling
if [ ! -f "$SCRIPT_DIR/state.sh" ]; then
    echo -e "${RED}ERROR: state.sh not found at $SCRIPT_DIR/state.sh${NC}" >&2
    exit 1
fi
. "$SCRIPT_DIR/state.sh"

# Verify is_checkpoint_done is available
if ! command -v is_checkpoint_done >/dev/null 2>&1; then
    echo -e "${RED}ERROR: is_checkpoint_done not defined after sourcing state.sh${NC}" >&2
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
