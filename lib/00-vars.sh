# 00-vars.sh — Pre-sourced before all other lib files (alphabetical order).
# Provides critical defaults so update.sh can git-pull even on older installs.
export SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
export SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

# Node-mode predicate used across lib/*.sh (docker, fresh_*, update paths).
# A missing definition made every call site fail with
# "is_node_mode: command not found" (2026-09-15). Mirrored in
# backend/install.sh (self-contained bundle) — keep the two in sync.
is_node_mode() { [ "${MODE_NODE:-false}" = "true" ]; }
