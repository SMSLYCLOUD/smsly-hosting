#!/bin/bash
# Grid by SMSLY - Update Mode Module
# Sourced by install.sh for --update

if [ -n "$UPDATE_MODE" ]; then
    source "$INSTALL_DIR/lib/update_preflight.sh"
    source "$INSTALL_DIR/lib/update_git.sh"
    source "$INSTALL_DIR/lib/update_rebuild.sh"
    source "$INSTALL_DIR/lib/update_post_deploy.sh"
fi
