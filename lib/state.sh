# ─── Installation State Machine ──────────────────────────────────────────────
STATE_FILE="/opt/smsly-hosting/.smsly_install_state"
STATE_MODE_FILE="${STATE_FILE}.mode"

install_flavor() {
    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        echo "agent-lite"
    else
        echo "master"
    fi
}

sync_install_state_flavor() {
    local current_flavor
    local previous_flavor
    current_flavor="$(install_flavor)"
    mkdir -p "$(dirname "$STATE_FILE")"

    if [ "$RESUME_MODE" = "true" ] && [ -f "$STATE_FILE" ]; then
        previous_flavor="$(cat "$STATE_MODE_FILE"  || echo "legacy")"
        if [ "$previous_flavor" != "$current_flavor" ]; then
            echo -e "${YELLOW}  -> Existing install checkpoints are for '$previous_flavor'; resetting state for '$current_flavor'.${NC}"
            rm -f "$STATE_FILE"
        fi
    fi

    printf '%s\n' "$current_flavor" > "$STATE_MODE_FILE"
}

set_checkpoint() {
    local name="$1"
    mkdir -p "$(dirname "$STATE_FILE")"
    printf '%s\n' "$(install_flavor)" > "$STATE_MODE_FILE"
    # Ensure name is unique in the file to avoid duplicates on resume
    if ! grep -q "^$name$" "$STATE_FILE" ; then
        echo "$name" >> "$STATE_FILE"
    fi
    echo -e "${GREEN}  ✓ Checkpoint reached: $name${NC}"
}

is_checkpoint_done() {
    local name="$1"
    if [ "$RESUME_MODE" != "true" ]; then
        return 1
    fi
    if [ -f "$STATE_FILE" ] && grep -q "^$name$" "$STATE_FILE"; then
        echo -e "${BLUE}  → Skipping already completed step: $name${NC}"
        return 0
    fi
    return 1
}

clear_checkpoint() {
    local name="$1"
    if [ -f "$STATE_FILE" ]; then
        grep -v "^$name$" "$STATE_FILE" > "${STATE_FILE}.tmp"  || true
        mv "${STATE_FILE}.tmp" "$STATE_FILE"  || true
    fi
}