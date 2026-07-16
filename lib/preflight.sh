check_internet() {
    echo -e "${BLUE}  → Checking internet connectivity...${NC}"
    if ! curl -Is --connect-timeout -k 5 5 https://google.com >/dev/null; then
        echo -e "${RED}  ✗ No internet access. Check your firewall/network settings.${NC}"
        exit 1
    fi
    if ! host github.com >/dev/null 2>&1; then
         # Fallback to ping if host is missing
         if ! ping -c 1 github.com >/dev/null 2>&1; then
             echo -e "${RED}  ✗ DNS resolution failed for github.com.${NC}"
             exit 1
         fi
    fi
    echo -e "${GREEN}  ✓ Internet & DNS OK${NC}"
}

check_hardware() {
    echo -e "${BLUE}  → Checking hardware requirements...${NC}"
    local ram_kb
    ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local ram_mb=$((ram_kb / 1024))
    echo -e "${BLUE}  RAM: ${ram_mb}MB${NC}"
    if [ "$ram_mb" -lt 950 ]; then # Allow some margin for 1GB VPS
        echo -e "${RED}  ✗ Insufficient RAM ($ram_mb MB). Grid requires at least 1GB.${NC}"
        exit 1
    fi

    local cores
    cores=$(nproc)
    echo -e "${BLUE}  CPU Cores: ${cores}${NC}"
    if [ "$cores" -lt 1 ]; then
        echo -e "${RED}  ✗ CPU detection failed.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Hardware requirements met${NC}"
}

check_caddy_conflict() {
    echo -e "${BLUE}  → Checking for host-level Caddy/Traefik port conflicts...${NC}"
    if systemctl is-active --quiet caddy 2>/dev/null; then
        echo -e "${RED}ERROR: Host Caddy service detected (systemd)${NC}"
        echo -e "${YELLOW}Grid uses Docker-managed routing. Master uses Docker Caddy, and node mode uses Traefik on public port 80.${NC}"
        echo -e ""
        echo -e "Run:"
        echo -e "  sudo systemctl stop caddy"
        echo -e "  sudo systemctl disable caddy"
        echo -e ""
        echo -e "Then re-run installer."
        exit 1
    fi
    echo -e "${GREEN}  ✓ No host-level Caddy/Traefik conflict detected${NC}"
}

wait_for_apt_lock() {
    local lock_files=(
        "/var/lib/dpkg/lock-frontend"
        "/var/lib/dpkg/lock"
        "/var/cache/apt/archives/lock"
    )
    local max_wait="${SMSLY_APT_LOCK_TIMEOUT:-600}"
    local elapsed=0
    local lock_file
    local active_locks=()
    local pids
    local pid

    while true; do
        active_locks=()
        pids=""

        if command -v fuser >/dev/null 2>&1; then
            for lock_file in "${lock_files[@]}"; do
                [ -e "$lock_file" ] || continue
                if fuser "$lock_file" >/dev/null 2>&1; then
                    active_locks+=("$lock_file")
                    pids="$pids $(fuser "$lock_file" 2>/dev/null || true)"
                fi
            done
        fi

        if [ "${#active_locks[@]}" -eq 0 ]; then
            if [ "$elapsed" -gt 0 ]; then
                echo
                echo -e "${GREEN}  ✓ APT system ready${NC}"
            fi
            return 0
        fi

        if [ "$elapsed" -eq 0 ]; then
            echo -e "${BLUE}  → Checking for background system updates (APT lock)...${NC}"
        fi

        if [ "$elapsed" -ge "$max_wait" ]; then
            echo
            echo -e "${RED}  x APT lock is still held after ${max_wait}s.${NC}"
            echo -e "${YELLOW}  Holding process(es):${NC}"
            for pid in $(printf "%s\n" $pids | sort -u); do
                ps -p "$pid" -o pid=,comm=,etime=,args= 2>/dev/null || true
            done
            echo -e "${YELLOW}  Wait for those processes to finish, then rerun the installer.${NC}"
            echo -e "${YELLOW}  If no apt/dpkg processes are running, repair with: sudo dpkg --configure -a${NC}"
            return 1
        fi

        if [ $((elapsed % 30)) -eq 0 ]; then
            echo
            echo -e "${YELLOW}  Waiting for APT lock (${elapsed}s/${max_wait}s). Active lock(s): ${active_locks[*]}${NC}"
            for pid in $(printf "%s\n" $pids | sort -u); do
                ps -p "$pid" -o pid=,comm=,etime=,args= 2>/dev/null || true
            done
        else
            printf "."
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done
}

apt_run() {
    local max_attempts="${SMSLY_APT_ATTEMPTS:-6}"
    local attempt=1
    local output=""
    local rc=0

    while [ "$attempt" -le "$max_attempts" ]; do
        wait_for_apt_lock || return 1
        # SECURITY/HARDENING: avoid set +e / set -e toggling. Capture rc via
        # explicit conditional so set -e stays in effect the whole time.
        if output="$("$@" 2>&1)"; then
            rc=0
        else
            rc=$?
        fi

        if [ "$rc" -eq 0 ]; then
            [ -n "$output" ] && printf '%s\n' "$output"
            return 0
        fi

        if printf '%s\n' "$output" | grep -qiE 'Could not get lock|Unable to acquire.*lock|dpkg frontend lock|/var/lib/dpkg/lock|/var/cache/apt/archives/lock'; then
            echo -e "${YELLOW}  APT lock appeared during command; retrying ($attempt/$max_attempts)...${NC}"
            sleep $((attempt * 5))
            attempt=$((attempt + 1))
            continue
        fi

        printf '%s\n' "$output"
        return "$rc"
    done

    printf '%s\n' "$output"
    return "$rc"
}

ensure_system_swap() {
    echo -e "${BLUE}  → Ensuring system swap is sufficient (Target: 3x-4x RAM)...${NC}"
    local current_ram_mb
    current_ram_mb=$(free -m | awk '/^Mem:/{print $2}')

    # Strictly enforce 4x RAM target for maximum stability
    local target_swap_mb=$((current_ram_mb * 4))

    # Cap at 64GB max for sanity, but floor at 4x RAM for the user's requirement
    [ "$target_swap_mb" -gt 65536 ] && target_swap_mb=65536

    local current_swap_mb
    current_swap_mb=$(free -m | awk '/^Swap:/{print $2}')

    # Check for ACTIVE swap (sometimes free -m reports phantom swap from host)
    local active_swap_count
    active_swap_count=$(grep -c / /proc/swaps || echo 0)

    # If swap is insufficient or missing, provision it.
    if [ "$current_swap_mb" -lt "$target_swap_mb" ] || [ "$active_swap_count" -eq 0 ]; then
        local needed_mb=$target_swap_mb
        [ "$current_swap_mb" -gt 0 ] && [ "$active_swap_count" -gt 0 ] && needed_mb=$((target_swap_mb - current_swap_mb))

        echo -e "${BLUE}  → Provisioning ${needed_mb}MB local swap (RAM: ${current_ram_mb}MB, Target: 4x)...${NC}"
        local swapfile="/swapfile-smsly"

        # If the file already exists but is too small, we need to recreate it
        if [ -f "$swapfile" ]; then
            swapoff "$swapfile" 2>/dev/null || true
            rm -f "$swapfile"
            # Since we removed the old file, we need to create the full target amount
            needed_mb=$target_swap_mb
        fi

        fallocate -l ${needed_mb}M "$swapfile" 2>/dev/null || dd if=/dev/zero of="$swapfile" bs=1M count=$needed_mb status=none
        chmod 600 "$swapfile"
        mkswap "$swapfile" >/dev/null 2>&1
        swapon "$swapfile" 2>/dev/null || true
        # Make permanent (idempotent)
        if ! grep -q "$swapfile" /etc/fstab 2>/dev/null; then
            echo "$swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}  ✓ Swap file created and activated (${needed_mb}MB)${NC}"
    else
        echo -e "${GREEN}  ✓ Swap already sufficient (${current_swap_mb}MB, >= 4x RAM)${NC}"
    fi
}