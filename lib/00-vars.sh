# 00-vars.sh — Pre-sourced before all other lib files (alphabetical order).
# Provides critical defaults so update.sh can git-pull even on older installs.
export SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
export SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"
