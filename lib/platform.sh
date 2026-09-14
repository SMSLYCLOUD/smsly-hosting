_SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
source "$_SCRIPT_DIR/platform-diagnostics.sh"
source "$_SCRIPT_DIR/platform-domain.sh"
source "$_SCRIPT_DIR/platform-env.sh"
source "$_SCRIPT_DIR/platform-validation.sh"
unset _SCRIPT_DIR
