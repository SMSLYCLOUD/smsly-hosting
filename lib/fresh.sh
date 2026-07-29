#!/bin/bash
# Grid by SMSLY - Fresh Install Mode Module
# Sourced by install.sh for fresh installs

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

export PATH="/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/state.sh"
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
