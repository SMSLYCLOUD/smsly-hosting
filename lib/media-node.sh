# lib/media-node.sh — Media Node provisioning functions
# Sourced by install.sh when --mode=media-node

[ "${_MEDIA_NODE_LOADED:-}" = "true" ] && return 0
_MEDIA_NODE_LOADED=true

MEDIA_NODE_INSTALL_DIR="/opt/smsly-hosting-media"
MEDIA_NODE_ENV="$MEDIA_NODE_INSTALL_DIR/.env"
MEDIA_NODE_LOG="/var/log/smsly-media-install.log"

# ─── Detect media hardware ───────────────────────────────────────────────────
detect_media_hardware() {
    echo -e "${BLUE}  → Detecting media hardware...${NC}"

    local cores
    cores=$(nproc  || echo 0)
    local ram_kb
    ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}'  || echo 0)
    local ram_mb=$((ram_kb / 1024))
    local disk_gb
    disk_gb=$(df -BG / | awk 'NR==2 {print $2}' | tr -d 'G'  || echo 0)

    echo -e "${BLUE}    CPU: ${cores} cores | RAM: ${ram_mb}MB | Disk: ${disk_gb}GB${NC}"

    if [ "$cores" -lt 4 ]; then
        echo -e "${RED}  ✗ Media node requires ≥4 CPU cores (found: ${cores})${NC}"
        return 1
    fi
    if [ "$ram_mb" -lt 7680 ]; then
        echo -e "${RED}  ✗ Media node requires ≥8GB RAM (found: ${ram_mb}MB)${NC}"
        return 1
    fi
    if [ "$disk_gb" -lt 50 ]; then
        echo -e "${RED}  ✗ Media node requires ≥50GB disk (found: ${disk_gb}GB)${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ Hardware requirements met${NC}"
}

# ─── Detect available ports ──────────────────────────────────────────────────
check_media_ports() {
    echo -e "${BLUE}  → Checking media ports...${NC}"
    local ports=(80 443 5060 5060/udp 3478 3478/udp 9090 9091 30000-31000/udp)
    local blocked=()

    for port_spec in "${ports[@]}"; do
        local port="${port_spec%%/*}"
        local proto="${port_spec##*/}"
        if [ "$proto" = "$port_spec" ]; then
            proto="tcp"
        fi

        if [ "$proto" = "udp" ]; then
            if ss -ulnp  | grep -q ":${port} " ; then
                blocked+=("$port_spec")
            fi
        else
            if ss -tlnp  | grep -q ":${port} " ; then
                blocked+=("$port_spec")
            fi
        fi
    done

    if [ ${#blocked[@]} -gt 0 ]; then
        echo -e "${RED}  ✗ Ports blocked: ${blocked[*]}${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ All media ports available${NC}"
}

# ─── Detect TPM ─────────────────────────────────────────────────────────────
detect_tpm() {
    if [ -c /dev/tpm0 ] && command -v tpm2_pcrread ; then
        echo -e "${GREEN}  ✓ TPM 2.0 detected${NC}"
        echo "tpm2"
    else
        echo -e "${YELLOW}  ⚠ No TPM 2.0 — using software fallback${NC}"
        echo "software"
    fi
}

# ─── Generate media node secrets ────────────────────────────────────────────
generate_media_secrets() {
    local env_file="$1"
    echo -e "${BLUE}  → Generating media node secrets...${NC}"

    local gateway_secret
    gateway_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local livekit_api_key
    livekit_api_key="$(openssl rand -hex 16  || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local livekit_api_secret
    livekit_api_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local turn_secret
    turn_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local postgres_password
    postgres_password="$(openssl rand -hex 16  || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local redis_password
    redis_password="$(openssl rand -hex 16  || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local jwt_secret
    jwt_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local webhook_secret
    webhook_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"

    cat > "$env_file" <<EOF
# SMSLY Media Node — Auto-generated secrets
# Generated: $(date -Iseconds)

NODE_TYPE=media
NODE_ID=${NODE_ID:-$(hostname -f  || hostname)}

# Master connection
MASTER_IP=${MASTER_IP:-}
MASTER_MESH_IP=${MASTER_MESH_IP:-}
MASTER_API_URL=${MASTER_API_URL:-https://master.smsly.com/api/v1}
GATEWAY_SECRET=${GATEWAY_SECRET:-$gateway_secret}

# Database (local)
POSTGRES_PASSWORD=${postgres_password}
REDIS_PASSWORD=${redis_password}
MEDIA_DB_USER=smsly_voice
MEDIA_DB_PASSWORD=${postgres_password}
DATABASE_URL=postgresql://smsly_voice:${postgres_password}@127.0.0.1:5432/smsly_voice
REDIS_URL=redis://127.0.0.1:6379
JWT_SECRET=${jwt_secret}
WEBHOOK_SECRET=${webhook_secret}
PORT=8002

# TURN
TURN_SECRET=${turn_secret}

# LiveKit SFU (rendered into /etc/livekit/livekit.yaml by install_livekit)
LIVEKIT_API_KEY=${livekit_api_key}
LIVEKIT_API_SECRET=${livekit_api_secret}
LIVEKIT_URL=ws://127.0.0.1:7880

# AI voice agent stack (all self-hosted, no cloud APIs)
WHISPER_MODEL=${WHISPER_MODEL:-base}
WHISPER_URL=http://127.0.0.1:8091
PIPER_URL=http://127.0.0.1:8091
LLM_PROVIDER=${LLM_PROVIDER:-ollama}
LLM_BASE_URL=${LLM_BASE_URL:-http://127.0.0.1:11434}
LLM_MODEL=${LLM_MODEL:-qwen2.5:3b}

    # Node identity
PUBLIC_IP=${PUBLIC_IP:-$(detect_public_ip  || echo "")}
# PRIVATE_IP is what Kamailio/coturn bind: prefer the first non-loopback
# local address (private NIC when present, else the public one). Never
# leave it at 127.0.0.1 on a real node — SIP/RTP would be unreachable.
PRIVATE_IP=${PRIVATE_IP:-$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | head -1 || echo "")}
PRIVATE_IP=${PRIVATE_IP:-${PUBLIC_IP:-127.0.0.1}}
DOMAIN=${DOMAIN:-$(hostname -f  || hostname)}

# Management daemon
CONFIG_PATH=/etc/smsly/media-mgmt.json
RUST_LOG=smsly_media_mgmt=info,tower_http=info
EOF

    chmod 600 "$env_file"
    echo -e "${GREEN}  ✓ Secrets generated${NC}"
}

# ─── Install media infrastructure packages ───────────────────────────────────
install_media_packages() {
    echo -e "${BLUE}  → Installing media infrastructure packages...${NC}"

    # Ensure smsly system user exists (all systemd units run as this user).
    # No hardcoded UID: cloud images often already take 1000 (e.g. the
    # default `ubuntu` user); a system UID picked by useradd is fine since
    # units reference the user by name.
    if ! id smsly >/dev/null 2>&1; then
        if useradd -r -s /usr/sbin/nologin smsly; then
            echo -e "${GREEN}  ✓ Created smsly system user${NC}"
        else
            echo -e "${RED}  ✗ Failed to create smsly system user — systemd units will fail to start${NC}"
        fi
    fi

    # Create required directories
    mkdir -p /var/log/smsly /run/smsly /var/lib/freeswitch /var/lib/livekit /var/log/coturn /var/lib/rtpengine-recording

    # Heal an interrupted dpkg from a previous killed run (half-configured
    # packages block every later apt invocation).
    dpkg --configure -a 2>&1 | tail -2 || true

    apt-get update -qq
    # NOTE: `postgresql` (no version) tracks the distro default (14 on
    # jammy, 16 on noble) — every media component talks stock SQL, so no
    # PGDG pin is needed. Asterisk ships in Ubuntu archives (fully OSS,
    # no token). openresty needs its own repo (warn-tolerant).
    # Never prompt on conffiles: Phase 3 deploys our configs over stock
    # paths, so re-runs must keep them (conffold) without asking.
    local -a apt_conf=(
        -o Dpkg::Options::="--force-confdef"
        -o Dpkg::Options::="--force-confold"
    )
    apt-get install -y -qq "${apt_conf[@]}" \
        postgresql \
        redis-server \
        wireguard \
        kamailio \
        kamailio-websocket-modules \
        kamailio-tls-modules \
        coturn \
        curl \
        jq \
        netcat-openbsd \
        gnupg \
        ca-certificates \
        build-essential \
        autoconf \
        automake \
        libtool \
        libopus-dev \
        pkg-config \
        libssl-dev \

    # ── OpenResty (official repo; edge proxy for media APIs) ──
    if ! command -v openresty >/dev/null 2>&1; then
        echo -e "${BLUE}  → Adding OpenResty repository...${NC}"
        if curl -fsSL https://openresty.org/package/pubkey.gpg 2>/dev/null | gpg --dearmor -o /usr/share/keyrings/openresty.gpg 2>/dev/null; then
            . /etc/os-release
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/openresty.gpg] http://openresty.org/package/ubuntu $VERSION_CODENAME main" > /etc/apt/sources.list.d/openresty.list
            if apt-get update -qq; then
                apt-get install -y -qq "${apt_conf[@]}" openresty || echo -e "${YELLOW}  ⚠ OpenResty install failed — edge proxy unavailable (non-fatal)${NC}"
            else
                echo -e "${YELLOW}  ⚠ OpenResty repo update failed — edge proxy unavailable (non-fatal)${NC}"
            fi
        else
            echo -e "${YELLOW}  ⚠ OpenResty key download failed — edge proxy unavailable (non-fatal)${NC}"
        fi
    fi

    # ── Asterisk (Ubuntu archive, no token/repo needed; fully OSS) ──
    # This stack is 100% token-free: Asterisk is the on-box B2BUA
    # (voicemail, conferencing, PSTN interop) behind Kamailio. It binds
    # 127.0.0.1:5080 only — Kamailio owns public :5060 and relays the
    # 5xx extension range to it (see infrastructure/media/kamailio).
    if ! command -v asterisk >/dev/null 2>&1; then
        echo -e "${BLUE}  → Installing Asterisk PBX...${NC}"
        apt-get install -y -qq "${apt_conf[@]}" asterisk \
            || echo -e "${YELLOW}  ⚠ Asterisk install failed — on-box PBX unavailable (non-fatal)${NC}"
    fi
        

    # Install RTPEngine (not in default Ubuntu repos — build from source or use PPA)
    if ! command -v rtpengine ; then
        echo -e "${BLUE}  → Installing RTPEngine...${NC}"
        apt-get install -y -qq rtpengine  || {
            echo -e "${YELLOW}  ⚠ RTPEngine not in apt repos — installing from Sipwise PPA...${NC}"
            apt-get install -y -qq software-properties-common  || true
            add-apt-repository -y ppa:sipwise/rtpengine  || true
            apt-get update -qq  && apt-get install -y -qq "${apt_conf[@]}" rtpengine  || {
                echo -e "${YELLOW}  ⚠ RTPEngine auto-install failed — install manually${NC}"
            }
        }
    fi

    echo -e "${GREEN}  ✓ Packages installed${NC}"
}

# ─── Stop listeners squatting the media ports ─────────────────────────────
# apt auto-starts kamailio/coturn/rtpengine/... with STOCK configs that grab
# the media ports; a repurposed box may also carry nginx/apache2 on :80.
# A media node is dedicated: stop + disable them all. Runs BEFORE the
# Phase-0 port check (a previous partial install leaves daemons behind)
# and again after package install (apt re-enables them). Phase 5 restarts
# only what the media configs define.
stop_stale_media_listeners() {
    echo -e "${BLUE}  → Clearing stale listeners from media ports...${NC}"
    for squat in kamailio coturn rtpengine asterisk freeswitch openresty nginx apache2; do
        systemctl stop "$squat" 2>/dev/null || true
        systemctl disable "$squat" 2>/dev/null || true
    done
    echo -e "${GREEN}  ✓ Stale listeners stopped (media owns :80/443/5060/3478)${NC}"
}

# ─── Deploy media configs ────────────────────────────────────────────────────
deploy_media_configs() {
    local script_dir="$1"
    echo -e "${BLUE}  → Deploying media node configs...${NC}"

    local infra_dir="$script_dir/infrastructure/media"
    if [ ! -d "$infra_dir" ]; then
        echo -e "${YELLOW}  ⚠ infrastructure/media/ not found — skipping config deployment${NC}"
        return 0
    fi

    # Required by systemd units with ProtectSystem=strict + ReadWritePaths
    # (e.g. smsly-media-mgmt needs /etc/smsly to exist or it exits 226).
    mkdir -p /etc/smsly
    chmod 755 /etc/smsly

    # Kamailio
    [ -d /etc/kamailio ] || mkdir -p /etc/kamailio
    cp -f "$infra_dir/kamailio/kamailio.cfg" /etc/kamailio/  || true
    cp -f "$infra_dir/kamailio/tls.cfg" /etc/kamailio/  || true

    # FreeSWITCH
    [ -d /etc/freeswitch ] && cp -f "$infra_dir/freeswitch/freeswitch.xml" /etc/freeswitch/  || true

    # Asterisk (on-box B2BUA behind Kamailio; binds loopback only)
    [ -d /etc/asterisk ] || mkdir -p /etc/asterisk
    cp -f "$infra_dir/asterisk/pjsip.conf" /etc/asterisk/  || true
    cp -f "$infra_dir/asterisk/extensions.conf" /etc/asterisk/  || true

    # coturn (was silently skipped before — stock config has no auth secret,
    # so TURN allocate always failed). Debian coturn reads
    # /etc/turnserver.conf by default.
    cp -f "$infra_dir/coturn/turnserver.conf" /etc/turnserver.conf  || true

    # RTPEngine
    [ -d /etc/rtpengine ] || mkdir -p /etc/rtpengine
    cp -f "$infra_dir/rtpengine/rtpengine.conf" /etc/rtpengine/  || true

    echo -e "${GREEN}  ✓ Media configs deployed${NC}"
}

# ─── Deploy media systemd units ───────────────────────────────────────────
# Units ship in <scripts-checkout>/scripts/systemd/ (smsly-media-mgmt,
# smsly-voice-api, smsly-video, coturn, rtpengine). Binaries for voice/video
# land in a later step; missing binaries only make those units fail at
# START time (warned, non-fatal) — mgmt + infra still come up.
deploy_media_systemd_units() {
    local script_dir="$1"
    echo -e "${BLUE}  → Deploying media systemd units...${NC}"

    local units_dir="$script_dir/scripts/systemd"
    if [ ! -d "$units_dir" ]; then
        echo -e "${YELLOW}  ⚠ scripts/systemd/ not found under $script_dir — skipping unit deployment${NC}"
        return 0
    fi

    local installed=0
    for unit in coturn.service rtpengine.service livekit-server.service smsly-media-mgmt.service smsly-voice-api.service smsly-video.service smsly-ai-services.service smsly-voicebot-orchestrator.service; do
        if [ -f "$units_dir/$unit" ]; then
            cp -f "$units_dir/$unit" /etc/systemd/system/
            echo -e "  → Installed ${unit}"
            installed=$((installed + 1))
        else
            echo -e "${YELLOW}  ⚠ Unit ${unit} not in $units_dir — skipping${NC}"
        fi
    done

    systemctl daemon-reload
    echo -e "${GREEN}  ✓ Systemd units deployed ($installed)${NC}"
}

# ─── Template env vars into config files ─────────────────────────────────────
template_media_configs() {
    local env_file="$1"
    echo -e "${BLUE}  → Templating env vars into media configs...${NC}"

    # Source env for values
    set -a
    source "$env_file"
    set +a

    # Kamailio
    if [ -f /etc/kamailio/kamailio.cfg ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${DOMAIN}|${DOMAIN}|g" \
            /etc/kamailio/kamailio.cfg
    fi

    # coturn (Debian default path)
    if [ -f /etc/turnserver.conf ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${TURN_SECRET}|${TURN_SECRET}|g" \
            -e "s|\${DOMAIN}|${DOMAIN}|g" \
            /etc/turnserver.conf
    fi

    # Asterisk trunk contact (Kamailio's IP)
    for _f in /etc/asterisk/pjsip.conf /etc/asterisk/extensions.conf; do
        if [ -f "$_f" ]; then
            sed -i \
                -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
                -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
                "$_f"
        fi
    done

    # RTPEngine
    if [ -f /etc/rtpengine/rtpengine.conf ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            /etc/rtpengine/rtpengine.conf
    fi

    # Attestation
    if [ -f /etc/smsly/attestation.json ]; then
        sed -i \
            -e "s|\${NODE_ID}|${NODE_ID}|g" \
            -e "s|\${POSTGRES_PASSWORD}|${POSTGRES_PASSWORD}|g" \
            -e "s|\${REDIS_PASSWORD}|${REDIS_PASSWORD}|g" \
            -e "s|\${MASTER_API_URL}|${MASTER_API_URL}|g" \
            -e "s|\${GATEWAY_SECRET}|${GATEWAY_SECRET}|g" \
            /etc/smsly/attestation.json
    fi

    echo -e "${GREEN}  ✓ Configs templated${NC}"
}

# ─── Start media services in order ───────────────────────────────────────────
start_media_services() {
    echo -e "${BLUE}  → Starting media services...${NC}"

    # A previous partial install can leave units in failed/rate-limited
    # state — clear it so `enable --now` below actually starts them.
    systemctl reset-failed 2>/dev/null || true

    local infra_services=(postgresql redis-server wireguard)
    local media_services=(kamailio rtpengine asterisk coturn livekit-server)
    local app_services=(smsly-voice-api smsly-video)
    local agent_services=(smsly-ai-services smsly-voicebot-orchestrator)
    local mgmt_services=(smsly-media-mgmt openresty)

    for svc in "${infra_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done
    sleep 2

    for svc in "${media_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done
    sleep 1

    for svc in "${app_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done

    for svc in "${agent_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done

    for svc in "${mgmt_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done

    echo -e "${GREEN}  ✓ All media services started${NC}"
}

# ─── Verify media services ───────────────────────────────────────────────────
verify_media_services() {
    echo -e "${BLUE}  → Verifying media services...${NC}"
    local failures=0

    local services=(
        "postgresql:pg_isready -q"
        "redis:redis-cli ping"
        "kamailio:ss -ulnp | grep -q ':5060 '"
        "asterisk:asterisk -rx 'core show version' >/dev/null 2>&1"
        "livekit-server:nc -z 127.0.0.1 7880"
        "smsly-ai-services:curl -sf http://127.0.0.1:8091/health"
        "smsly-voicebot-orchestrator:curl -sf http://127.0.0.1:3001/health"
        "smsly-media-mgmt:curl -sf http://127.0.0.1:9090/health"
    )

    for entry in "${services[@]}"; do
        local name="${entry%%:*}"
        # Strip up to the FIRST colon only — checks contain URLs
        # (http://127.0.0.1:9090/...) where ## would mangle the scheme.
        local check="${entry#*:}"
        if eval "$check" ; then
            echo -e "  ${GREEN}✓${NC} ${name}"
        else
            echo -e "  ${RED}✗${NC} ${name}"
            failures=$((failures + 1))
        fi
    done

    if [ "$failures" -gt 0 ]; then
        echo -e "${RED}  ✗ ${failures} services failed verification${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ All services healthy${NC}"
}

# ─── Full media node fresh install ───────────────────────────────────────────
build_media_mgmt() {
    local script_dir="$1"
    echo -e "${BLUE}  -> Setting up smsly-media-mgmt...${NC}"
    
    if [ -n "${MEDIA_REPO_URL:-}" ]; then
        local clone_url="${MEDIA_REPO_URL}"
        if [ -n "${MEDIA_REPO_TOKEN:-}" ]; then
            if [[ "$clone_url" =~ ^https:// ]]; then
                clone_url="https://oauth2:${MEDIA_REPO_TOKEN}@${clone_url#https://}"
            fi
        fi
        
        local custom_mgmt_dir="/opt/smsly-media-mgmt"
        if [ -d "$custom_mgmt_dir/.git" ]; then
            cd "$custom_mgmt_dir" && git pull --ff-only || echo -e "${YELLOW}  [WARN] git pull failed - using local copy${NC}"
        else
            git clone "$clone_url" "$custom_mgmt_dir" || echo -e "${RED}  ERROR: Failed to clone custom media repo${NC}"
        fi
        local mgmt_dir="$custom_mgmt_dir"
    else
        local mgmt_dir="$script_dir/../smsly-media-mgmt"
        if [ -d "$mgmt_dir/.git" ]; then
            cd "$mgmt_dir" && git pull --ff-only  || {
                echo -e "${YELLOW}  [WARN] git pull failed -- using local copy${NC}"
            }
        fi
    fi

    if [ -f "$mgmt_dir/Cargo.toml" ]; then
        echo -e "${BLUE}  -> Rebuilding smsly-media-mgmt...${NC}"

        if ! command -v cargo >/dev/null; then
            echo -e "${BLUE}  -> Installing Rust toolchain...${NC}"
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y >/dev/null
            export PATH="$HOME/.cargo/bin:$PATH"
        fi

        cd "$mgmt_dir" && cargo build --release 2>&1 | tail -20 || true
        if [ ! -f target/release/smsly-media-mgmt ]; then
            echo -e "${RED}  ✗ smsly-media-mgmt build produced no binary — see build output above${NC}"
            return 1
        fi
        if [ -f target/release/smsly-media-mgmt ]; then
            cp target/release/smsly-media-mgmt /usr/local/bin/smsly-media-mgmt
            systemctl restart smsly-media-mgmt 2>/dev/null || true
            echo -e "${GREEN}  [OK] smsly-media-mgmt updated${NC}"
        fi
    else
        echo -e "${YELLOW}  [WARN] smsly-media-mgmt not found or missing Cargo.toml at $mgmt_dir${NC}"
    fi
}

prepare_media_voice_database() {
    # DB creds live in the node .env (written by generate_media_secrets in
    # Phase 2) — they are NOT in this shell's environment otherwise, and
    # referencing them unset under `set -u` kills the installer silently.
    local env_file="${MEDIA_NODE_ENV:-/opt/smsly-hosting-media/.env}"
    if [ -f "$env_file" ]; then
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
    fi
    local db_user="${MEDIA_DB_USER:-smsly_voice}"
    local db_password="${MEDIA_DB_PASSWORD:-}"
    local db_name="smsly_voice"
    if [ -z "$db_password" ]; then
        echo -e "${RED}  ✗ MEDIA_DB_PASSWORD is empty — voice schema cannot be prepared (re-run Phase 2?)${NC}"
        return 1
    fi

    systemctl enable --now postgresql >/dev/null 2>&1 || true
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c \
        "DO \$\$ BEGIN CREATE ROLE ${db_user} LOGIN PASSWORD '${db_password}'; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;" \
        >/dev/null
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c \
        "ALTER ROLE ${db_user} PASSWORD '${db_password}';" >/dev/null
    runuser -u postgres -- createdb -O "$db_user" "$db_name" 2>/dev/null || true

    # Migration ledger — re-runs must skip files that already applied
    # (001_initial.sql has bare CREATE TYPEs that fail on second apply).
    export PGPASSWORD="$db_password"
    psql -h 127.0.0.1 -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 -c \
        "CREATE TABLE IF NOT EXISTS smsly_media_schema_migrations (filename text PRIMARY KEY, applied_at timestamptz DEFAULT NOW());" >/dev/null

    local migration mig_name applied
    for migration in "${MEDIA_VOICE_SOURCE_DIR}/storage/migrations/"*.sql; do
        [ -f "$migration" ] || continue
        mig_name="$(basename "$migration")"
        applied="$(psql -h 127.0.0.1 -U "$db_user" -d "$db_name" -tAc \
            "SELECT 1 FROM smsly_media_schema_migrations WHERE filename='$mig_name';")"
        if [ "$applied" = "1" ]; then
            echo -e "${BLUE}  → Migration $mig_name already applied, skipping${NC}"
            continue
        fi
        echo -e "${BLUE}  → Applying migration $mig_name...${NC}"
        if psql -h 127.0.0.1 -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 \
                -f "$migration" >/tmp/smsly-voice-migrate.log 2>&1; then
            psql -h 127.0.0.1 -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 -c \
                "INSERT INTO smsly_media_schema_migrations (filename) VALUES ('$mig_name');" >/dev/null
        else
            tail -20 /tmp/smsly-voice-migrate.log
            echo -e "${RED}  ✗ Migration $mig_name failed${NC}"
            unset PGPASSWORD
            return 1
        fi
    done
    unset PGPASSWORD
}

build_media_applications() {
    local voice_dir="${MEDIA_VOICE_SOURCE_DIR:-}"
    local video_dir="${MEDIA_VIDEO_SOURCE_DIR:-}"
    local attestation_dir="${MEDIA_ATTESTATION_SOURCE_DIR:-}"
    [ -d "$voice_dir" ] || { echo -e "${RED}  ✗ Voice source was not staged${NC}"; return 1; }
    [ -d "$video_dir" ] || { echo -e "${RED}  ✗ Video source was not staged${NC}"; return 1; }
    [ -f "$attestation_dir/Cargo.toml" ] || { echo -e "${RED}  ✗ Attestation source was not staged${NC}"; return 1; }

    if ! command -v cargo >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y >/dev/null
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    echo -e "${BLUE}  → Preparing voice schema for SQLx compile-time queries...${NC}"
    prepare_media_voice_database

    echo -e "${BLUE}  → Building smsly-voice-api...${NC}"
    cd "$voice_dir"
    DATABASE_URL="${DATABASE_URL}" cargo build --release --bin smsly-voice-api >/tmp/smsly-voice-build.log 2>&1 || {
        tail -60 /tmp/smsly-voice-build.log
        return 1
    }
    install -o smsly -g smsly -m 0755 target/release/smsly-voice-api /usr/local/bin/smsly-voice-api

    echo -e "${BLUE}  → Building smsly-api video service...${NC}"
    cd "$video_dir"
    cargo build --release -p smsly-api >/tmp/smsly-video-build.log 2>&1 || {
        tail -60 /tmp/smsly-video-build.log
        return 1
    }
    install -o smsly -g smsly -m 0755 target/release/smsly-api /usr/local/bin/smsly-api
}

# ─── Install LiveKit SFU server ────────────────────────────────────────────
# LiveKit ships no apt package: pin a GitHub release tarball, verify its
# sha256, and render /etc/livekit/livekit.yaml from the node .env (API
# keys are generated in Phase 2). TURN stays on standalone coturn, so the
# built-in TURN relay is disabled; RTC media uses UDP 30000-31000 to match
# the pre-flight port check.
LIVEKIT_VERSION="${LIVEKIT_VERSION:-v1.13.6}"
LIVEKIT_SHA256_AMD64="2b61abef2b9ba14b4b8ca38b37de9a37ffc682b9931d5fc03ceca2f0b77d3e33"
LIVEKIT_SHA256_ARM64="5c75f09173199f3f8fe0c3c0d5a41171f9b306ffce6843d752c276d11e77d19b"

install_livekit() {
    local env_file="${1:-/opt/smsly-hosting-media/.env}"
    echo -e "${BLUE}  → Installing LiveKit server ${LIVEKIT_VERSION}...${NC}"

    local arch
    case "$(dpkg --print-architecture 2>/dev/null || uname -m)" in
        amd64|x86_64) arch="amd64"; local want_sha="$LIVEKIT_SHA256_AMD64" ;;
        arm64|aarch64) arch="arm64"; local want_sha="$LIVEKIT_SHA256_ARM64" ;;
        *) echo -e "${YELLOW}  ⚠ Unsupported architecture for LiveKit binaries (non-fatal)${NC}"; return 0 ;;
    esac

    if command -v livekit-server >/dev/null 2>&1 && livekit-server --version 2>/dev/null | grep -q "$LIVEKIT_VERSION"; then
        echo -e "${GREEN}  ✓ LiveKit ${LIVEKIT_VERSION} already installed${NC}"
    else
        local url="https://github.com/livekit/livekit/releases/download/${LIVEKIT_VERSION}/livekit_${LIVEKIT_VERSION#v}_linux_${arch}.tar.gz"
        rm -f /tmp/livekit.tgz
        if ! curl -fsSL --max-time 300 "$url" -o /tmp/livekit.tgz; then
            echo -e "${YELLOW}  ⚠ LiveKit download failed — WebRTC SFU unavailable (non-fatal)${NC}"
            return 0
        fi
        local got_sha
        got_sha="$(sha256sum /tmp/livekit.tgz | awk '{print $1}')"
        if [ "$got_sha" != "$want_sha" ]; then
            echo -e "${YELLOW}  ⚠ LiveKit checksum mismatch (got ${got_sha:0:12}…) — refusing to install (non-fatal)${NC}"
            rm -f /tmp/livekit.tgz
            return 0
        fi
        tar -xzf /tmp/livekit.tgz -C /tmp livekit-server
        install -o root -g root -m 0755 /tmp/livekit-server /usr/local/bin/livekit-server
        rm -f /tmp/livekit.tgz /tmp/livekit-server
        echo -e "${GREEN}  ✓ LiveKit ${LIVEKIT_VERSION} installed${NC}"
    fi

    # Render config (idempotent — re-run picks up rotated keys).
    [ -f "$env_file" ] && { set -a; source "$env_file"; set +a; }
    mkdir -p /etc/livekit /var/lib/livekit
    cat > /etc/livekit/livekit.yaml <<EOF
port: 7880
bind_addresses:
  - "0.0.0.0"
rtc:
  tcp_port: 7881
  port_range_start: 30000
  port_range_end: 31000
  use_external_ip: true
  node_ip: "${PUBLIC_IP:-127.0.0.1}"
keys:
  "${LIVEKIT_API_KEY:-devkey}": "${LIVEKIT_API_SECRET:-secret}"
room:
  empty_timeout: 300
  max_participants: 200
turn:
  enabled: false
EOF
    chmod 640 /etc/livekit/livekit.yaml
    chown root:smsly /etc/livekit/livekit.yaml
    echo -e "${GREEN}  ✓ LiveKit config rendered${NC}"
}

# ─── Install AI voice agent stack (all self-hosted) ───────────────────────
# Three OSS pieces, zero cloud APIs:
#   1. Ollama + open model (default qwen2.5:3b) for the LLM leg.
#   2. smsly-ai-services: one Python daemon serving faster-whisper STT
#      (/inference) and Piper TTS (/synthesize) on 127.0.0.1:8091.
#   3. smsly-voicebot-orchestrator (Rust): joins LiveKit rooms and runs
#      the listen → STT → LLM → TTS → speak loop.
install_agent_stack() {
    local script_dir="$1"
    echo -e "${BLUE}  → Installing AI voice agent stack...${NC}"

    # 1. Ollama (official install script, no token needed)
    if ! command -v ollama >/dev/null 2>&1; then
        echo -e "${BLUE}  → Installing Ollama...${NC}"
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    systemctl enable --now ollama 2>/dev/null || true
    local llm_model="${LLM_MODEL:-qwen2.5:3b}"
    echo -e "${BLUE}  → Pulling LLM model ${llm_model} (one-time, ~2GB)...${NC}"
    if ! ollama pull "$llm_model" 2>&1 | tail -2; then
        echo -e "${YELLOW}  ⚠ LLM model pull failed — agent falls back when Ollama is ready (non-fatal)${NC}"
    fi

    # 2. STT+TTS daemon (venv keeps apt Python pristine). Source lives in
    # the staged voice tree; /opt/smsly-voice-src is the canonical path
    # the provisioner stages (GitHub App fetch, no node-side credentials).
    local ai_dir="/opt/smsly-ai"
    local ai_src="/opt/smsly-voice-src/infrastructure/voicebot/ai-services"
    if [ ! -f "$ai_src/server.py" ]; then
        echo -e "${YELLOW}  ⚠ ai-services source not found — skipping STT/TTS daemon (non-fatal)${NC}"
    else
        mkdir -p "$ai_dir/models" "$ai_dir/voices" "$ai_dir/hf-cache"
        chown -R smsly:smsly "$ai_dir"
        if [ ! -x "$ai_dir/venv/bin/python" ]; then
            python3 -m venv "$ai_dir/venv"
        fi
        "$ai_dir/venv/bin/pip" install -q -r "$ai_src/requirements.txt"
        cp -f "$ai_src/server.py" "$ai_dir/server.py"
        chown smsly:smsly "$ai_dir/server.py"
        # Pre-download models so first calls never block on network.
        sudo -u smsly HF_HOME="$ai_dir/hf-cache" "$ai_dir/venv/bin/python" -c \
            "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL:-base}')" 2>&1 | tail -1 || true
        if [ ! -f "$ai_dir/voices/en_US-lessac-medium.onnx" ]; then
            curl -fsSL --max-time 300 \
                "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx" \
                -o "$ai_dir/voices/en_US-lessac-medium.onnx" || true
            curl -fsSL --max-time 120 \
                "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" \
                -o "$ai_dir/voices/en_US-lessac-medium.onnx.json" || true
            chown smsly:smsly "$ai_dir/voices/"* 2>/dev/null || true
        fi
        echo -e "${GREEN}  ✓ AI speech services staged${NC}"
    fi

    # 3. Orchestrator binary (built from the staged voice tree). Its WebRTC
    # dependency needs clang 21+ (Ubuntu noble ships 18): bootstrap it from
    # apt.llvm.org once, then reuse for every later build on this box.
    # webrtc-sys also needs glib/ALSA headers for its build scripts.
    local orch_src="/opt/smsly-voice-src/infrastructure/voicebot/ai-orchestrator"
    if [ ! -f "$orch_src/Cargo.toml" ]; then
        echo -e "${YELLOW}  ⚠ orchestrator source not found — skipping build (non-fatal)${NC}"
    else
        apt-get install -y -qq libglib2.0-dev libasound2-dev 2>&1 | tail -1 || true
        if ! command -v clang++-21 >/dev/null 2>&1; then
            echo -e "${BLUE}  → Installing clang-21 (WebRTC build requirement)...${NC}"
            curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key 2>/dev/null \
                | gpg --dearmor -o /usr/share/keyrings/llvm.gpg 2>/dev/null || true
            echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] http://apt.llvm.org/noble/ llvm-toolchain-noble-21 main" \
                > /etc/apt/sources.list.d/llvm.list
            apt-get update -qq && apt-get install -y -qq clang-21 || true
        fi
        export PATH="$HOME/.cargo/bin:$PATH"
        export CC=clang-21 CXX=clang++-21
        echo -e "${BLUE}  → Building voicebot orchestrator (one-time, ~25 min, mostly WebRTC C++)...${NC}"
        if (cd "$orch_src" && cargo build --release 2>&1 | tail -5); then
            # Stop first: overwriting a running binary fails with ETXTBSY.
            # start_media_services (later phase) brings it back up.
            systemctl stop smsly-voicebot-orchestrator 2>/dev/null || true
            install -o smsly -g smsly -m 0755 \
                "$orch_src/target/release/smsly-voicebot-orchestrator" \
                /usr/local/bin/smsly-voicebot-orchestrator
            echo -e "${GREEN}  ✓ Orchestrator installed${NC}"
        else
            echo -e "${YELLOW}  ⚠ Orchestrator build failed — agent calls unavailable until rebuilt (non-fatal)${NC}"
        fi
    fi
}

install_media_node() {
    local script_dir="$1"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Fresh Install${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    # Phase 0: Pre-flight
    check_internet
    detect_media_hardware
    check_media_ports

    # Phase 1: Core infrastructure
    install_media_packages

    # Phase 2: Create install dir + generate secrets
    mkdir -p "$MEDIA_NODE_INSTALL_DIR"
    generate_media_secrets "$MEDIA_NODE_ENV"

    # Phase 2.5: Build custom Rust daemon
    build_media_mgmt "$script_dir"

    # Phase 3: Deploy configs + systemd
    deploy_media_configs "$script_dir"
    deploy_media_systemd_units "$script_dir"

    # Phase 4: Template configs
    template_media_configs "$MEDIA_NODE_ENV"

    # Phase 5: Start services
    start_media_services

    # Phase 6: Verify
    sleep 3
    verify_media_services

    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Media node installation complete${NC}"
    echo -e "${GREEN}  → Config: ${MEDIA_NODE_ENV}${NC}"
    echo -e "${GREEN}  → Logs:   ${MEDIA_NODE_LOG}${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
}

# ─── Media node update (rebuild binaries, restart services) ───────────────────
update_media_node() {
    local script_dir="$1"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Update${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    if [ ! -d "$script_dir" ]; then
        echo -e "${RED}  ERROR: Script directory not found: $script_dir${NC}"
        exit 1
    fi

    # Pull latest code and build Rust daemon
    build_media_mgmt "$script_dir"

    # Redeploy configs
    echo -e "${BLUE}  → Updating configs...${NC}"
    deploy_media_configs "$script_dir"
    deploy_media_systemd_units "$script_dir"
    if [ -f "$MEDIA_NODE_ENV" ]; then
        template_media_configs "$MEDIA_NODE_ENV"
    fi

    # Restart all media services
    echo -e "${BLUE}  → Restarting media services...${NC}"
    for svc in smsly-media-mgmt smsly-voice-api smsly-video livekit-server rtpengine asterisk kamailio coturn openresty; do
        systemctl restart "$svc" || echo -e "${YELLOW}    ⚠ systemctl restart $svc failed${NC}"
    done

    sleep 3
    verify_media_services

    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Media node update complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
}
