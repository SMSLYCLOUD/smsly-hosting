gen_hex_secret() {
    local bytes="${1:-16}"
    python3 -c "import secrets; print(secrets.token_hex(${bytes}))"  || openssl rand -hex "$bytes"
}

env_load_file_safe() {
    local env_file="${1:-}"
    [ -f "$env_file" ] || return 0
    local _line="" _key="" _val=""
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line="${_line%$'\r'}"
        [[ -z "$_line" || "$_line" =~ ^[[:space:]]*# ]] && continue
        [[ "$_line" == export[[:space:]]* ]] && _line="${_line#export }"
        [[ "$_line" == *=* ]] || continue
        _key="${_line%%=*}"
        _key="${_key#"${_key%%[![:space:]]*}"}"
        _key="${_key%"${_key##*[![:space:]]}"}"
        [[ "$_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        _val="${_line#*=}"
        if [[ "$_val" == \"*\" && "$_val" == *\" && ${#_val} -ge 2 ]]; then
            _val="${_val:1:${#_val}-2}"
            _val="${_val//\\\"/\"}"
            _val="${_val//\\\$/\$}"
            _val="${_val//\\\`/\`}"
            _val="${_val//\\\\/\\}"
        elif [[ "$_val" == \'*\' && "$_val" == *\' && ${#_val} -ge 2 ]]; then
            _val="${_val:1:${#_val}-2}"
        fi
        export "$_key=$_val"
    done < "$env_file"
}

env_get_value() {
    local env_file="$1"
    local var_name="$2"
    grep -m1 "^${var_name}=" "$env_file"  | cut -d= -f2- | sed 's/^"//;s/"$//;s/^'\''//;s/'\''$//' || true
}

env_set_value() {
    local env_file="$1"
    local var_name="$2"
    local var_value="$3"
    python3 - "$env_file" "$var_name" "$var_value" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
prefix = f"{key}="

def _format_val(val: str) -> str:
    if val == "":
        return ""
    if any(c.isspace() or c in '#$`\\"\'&;|<>()*?[]~!' for c in val):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'"{escaped}"'
    return val

formatted = _format_val(value)

if not env_path.exists():
    env_path.write_text(f"{key}={formatted}\n")
    sys.exit(0)

lines = env_path.read_text().splitlines()
updated = []
found = False

for line in lines:
    if line.startswith(prefix):
        if not found:
            updated.append(f"{key}={formatted}")
            found = True
        # Skip any subsequent duplicates
        continue
    updated.append(line)

if not found:
    updated.append(f"{key}={formatted}")

env_path.write_text("\n".join(updated) + "\n")
PY
}

sanitize_node_identifier() {
    local value="${1:-}"
    value="$(printf '%s' "$value" | tr -c 'A-Za-z0-9_.-' '-' | sed -E 's/^-+//; s/-+$//; s/-+/-/g' | cut -c1-96)"
    if [ -z "$value" ]; then
        value="$(hostname  | tr -c 'A-Za-z0-9_.-' '-' | sed -E 's/^-+//; s/-+$//; s/-+/-/g' | cut -c1-96)"
    fi
    [ -n "$value" ] || value="agent"
    printf '%s' "$value"
}

env_append_csv_values() {
    local env_file="$1"
    local var_name="$2"
    shift 2

    python3 - "$env_file" "$var_name" "$@" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
requested = [value.strip() for value in sys.argv[3:] if value.strip()]
prefix = f"{key}="

lines = env_path.read_text().splitlines() if env_path.exists() else []
updated = []
found = False
changed = False

for line in lines:
    if line.startswith(prefix):
        if not found:
            values = [value.strip() for value in line[len(prefix):].split(",") if value.strip()]
            seen = {value.lower() for value in values}
            for value in requested:
                if value.lower() not in seen:
                    values.append(value)
                    seen.add(value.lower())
                    changed = True
            updated.append(f"{key}={','.join(values)}")
            found = True
        else:
            changed = True
        continue
    updated.append(line)

if not found:
    updated.append(f"{key}={','.join(requested)}")
    changed = True

if changed:
    env_path.write_text("\n".join(updated) + "\n")

print("changed" if changed else "unchanged")
PY
}

sync_env_domain_allowlists() {
    local env_file="$1"
    local domain="${2:-}"
    local public_ip="${3:-}"
    local changed=false
    local result=""
    local allowed_hosts=("localhost" "127.0.0.1" "backend" "smsly-hosting-backend-1")
    local csrf_origins=("http://localhost:8090")
    local cors_origins=("http://localhost:8090")

    [ -f "$env_file" ] || return 0

    [ -n "$domain" ] || domain="$(env_get_value "$env_file" "DOMAIN")"
    [ -n "$public_ip" ] || public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"

    if [ -n "$domain" ]; then
        allowed_hosts+=("$domain")
        csrf_origins+=("https://${domain}" "http://${domain}")
        cors_origins+=("https://${domain}" "http://${domain}")
    fi

    if [ -n "$public_ip" ]; then
        allowed_hosts+=("$public_ip")
        csrf_origins+=("http://${public_ip}:8090" "http://${public_ip}")
        cors_origins+=("http://${public_ip}:8090" "http://${public_ip}")
    fi

    # Automatically add all node IPs (including WireGuard VPN mesh IPs like 10.100.x.x)
    local current_ips
    current_ips="$(hostname -I  | tr -s ' ' '\n' | grep -v '^$' || true)"
    if [ -n "$current_ips" ]; then
        for ip in $current_ips; do
            allowed_hosts+=("$ip")
            csrf_origins+=("http://${ip}:8090" "http://${ip}" "https://${ip}")
            cors_origins+=("http://${ip}:8090" "http://${ip}" "https://${ip}")
        done
    fi

    result="$(env_append_csv_values "$env_file" "ALLOWED_HOSTS" "${allowed_hosts[@]}")"
    [ "$result" = "changed" ] && changed=true
    result="$(env_append_csv_values "$env_file" "CSRF_TRUSTED_ORIGINS" "${csrf_origins[@]}")"
    [ "$result" = "changed" ] && changed=true
    result="$(env_append_csv_values "$env_file" "CORS_ALLOWED_ORIGINS" "${cors_origins[@]}")"
    [ "$result" = "changed" ] && changed=true

    if [ "$changed" = true ]; then
        echo -e "${GREEN}  ✓ Synced domain allowlists in .env${NC}"
    fi
}

env_ensure_var() {
    local env_file="$1"
    local var_name="$2"
    local var_value="$3"
    local var_comment="${4:-}"
    local current_val
    current_val="$(env_get_value "$env_file" "$var_name")"

    if [ -z "$current_val" ]; then
        echo -e "${BLUE}  -> Setting $var_name in .env${NC}"
        [ -n "$var_comment" ] && ! grep -q "# $var_comment" "$env_file"  && echo "# $var_comment" >> "$env_file"
        env_set_value "$env_file" "$var_name" "$var_value"
        echo -e "${GREEN}  OK $var_name set${NC}"
    fi
}
