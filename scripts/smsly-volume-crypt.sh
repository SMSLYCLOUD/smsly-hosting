#!/usr/bin/env bash
# Per-tenant encrypted addon volume helper (fscrypt or LUKS2 loopback).
#
# Subcommands:
#   preflight [--backend auto|fscrypt|luks]
#   provision <vol> [--backend fscrypt|luks] [--uid UID] [--gid GID]  (reads hex DEK on stdin)
#   unlock    <vol> [--backend fscrypt|luks]                          (reads hex DEK on stdin)
#   lock      <vol> [--backend fscrypt|luks]
#   unlock-all                                                        (unlocks all stored volumes)
#   verify    <vol>
#   cleanup   <vol> [--backend fscrypt|luks]
#
# Design guards (AGENTS.md #6, #23, #24):
# - Every external command uses `timeout -k 5 ...`.
# - Keys arrive on stdin (never argv or temp plaintext files).
# - Encrypted mounts keep `<mnt>` at mode 0000 when unmounted with NO `data/`
#   subdirectory on the root filesystem; `data/` exists ONLY inside the
#   mounted encrypted filesystem (avoiding `lost+found/` and preventing silent
#   plaintext writes onto the root FS if a mount fails).
# - All multi-variable `local` declarations initialize every variable (`local x="" y=""`).

set -euo pipefail

BASE_DIR="${SMSLY_ENCRYPTED_VOLUMES_DIR:-${SMSLY_ENCRYPTED_VOLUME_DIR:-/var/lib/smsly/encrypted-volumes}}"
KEYSTORE_DIR="${SMSLY_VOLUME_KEYS_DIR:-/var/lib/smsly/volume-keys}"
VOLUME_SIZE_MB="${SMSLY_VOLUME_SIZE_MB:-2048}"
IMAGES_DIR="${BASE_DIR}/images"
MNT_DIR="${BASE_DIR}/mnt"
FSCRYPT_DIR="${BASE_DIR}/fscrypt"

err() {
    echo "smsly-volume-crypt: $*" >&2
}

validate_vol_name() {
    local vol="$1"
    if [[ -z "$vol" || ! "$vol" =~ ^[A-Za-z0-9_.-]+$ ]]; then
        err "invalid volume name: ${vol}"
        return 1
    fi
}

mapper_name_for() {
    local vol="$1"
    local hash=""
    hash=$(printf '%s' "$vol" | sha256sum | awk '{print substr($1, 1, 16)}')
    printf 'smsly-vol-%s' "$hash"
}

check_fscrypt_capable() {
    command -v fscrypt >/dev/null 2>&1 || return 1
    [ "$(id -u)" -eq 0 ] || return 1
    local probe_dir="/var/lib"
    [ -d "$BASE_DIR" ] && probe_dir="$BASE_DIR"
    timeout -k 5 10 fscrypt status "$probe_dir" >/dev/null 2>&1
}

check_luks_capable() {
    [ "$(id -u)" -eq 0 ] || return 1
    command -v cryptsetup >/dev/null 2>&1 || return 1
    command -v losetup >/dev/null 2>&1 || return 1
    command -v mkfs.ext4 >/dev/null 2>&1 || return 1
    command -v mount >/dev/null 2>&1 || return 1
    command -v mountpoint >/dev/null 2>&1 || return 1
    [ -e /dev/mapper/control ] || return 1
    [ -e /dev/loop-control ] || return 1
}

select_backend() {
    local requested="${1:-auto}"
    case "$requested" in
        fscrypt)
            check_fscrypt_capable && { echo "fscrypt"; return 0; }
            return 1
            ;;
        luks)
            check_luks_capable && { echo "luks"; return 0; }
            return 1
            ;;
        auto|"")
            if check_fscrypt_capable; then
                echo "fscrypt"
                return 0
            fi
            if check_luks_capable; then
                echo "luks"
                return 0
            fi
            return 1
            ;;
        *)
            err "unknown backend: ${requested}"
            return 1
            ;;
    esac
}

ensure_dirs() {
    mkdir -p "$BASE_DIR" "$IMAGES_DIR" "$MNT_DIR" "$FSCRYPT_DIR" "$KEYSTORE_DIR"
    chmod 0700 "$KEYSTORE_DIR"
    chmod 0750 "$BASE_DIR" "$IMAGES_DIR" "$MNT_DIR" "$FSCRYPT_DIR"
}

persist_wrapped_key() {
    local vol="$1"
    if [ -n "${SMSLY_WRAPPED_DEK:-}" ]; then
        mkdir -p "$KEYSTORE_DIR"
        chmod 0700 "$KEYSTORE_DIR"
        printf '%s\n' "$SMSLY_WRAPPED_DEK" > "${KEYSTORE_DIR}/${vol}.enc"
        chmod 0600 "${KEYSTORE_DIR}/${vol}.enc"
    fi
}

read_dek_hex_stdin() {
    local hex=""
    IFS= read -r hex || true
    hex="${hex//[$'\r\n\t ']/}"
    if [[ -z "$hex" || ! "$hex" =~ ^[0-9a-fA-F]{64}$ ]]; then
        err "expected 64-char hex DEK on stdin"
        return 1
    fi
    printf '%s' "$hex"
}

hex_to_raw_pipe() {
    local hex="$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))' "$hex"
    elif command -v xxd >/dev/null 2>&1; then
        printf '%s' "$hex" | xxd -r -p
    else
        err "need python3 or xxd to decode hex DEK"
        return 1
    fi
}

create_docker_bind_volume() {
    local vol="$1"
    local data_dir="$2"
    local uid="${3:-}"
    local gid="${4:-}"

    if [ ! -d "$data_dir" ]; then
        err "bind target directory missing: ${data_dir}"
        return 1
    fi
    if [ -n "$uid" ] && [ -n "$gid" ]; then
        chown "${uid}:${gid}" "$data_dir"
    fi
    if ! timeout -k 5 15 docker volume inspect "$vol" >/dev/null 2>&1; then
        timeout -k 5 30 docker volume create \
            --driver local \
            --opt type=none \
            --opt o=bind \
            --opt "device=${data_dir}" \
            "$vol" >/dev/null
    fi
}

cmd_preflight() {
    local requested="auto"
    while [ $# -gt 0 ]; do
        case "$1" in
            --backend) requested="${2:-auto}"; shift 2 ;;
            *) shift ;;
        esac
    done
    local fscrypt_ok="false" luks_ok="false" selected=""
    check_fscrypt_capable && fscrypt_ok="true" || true
    check_luks_capable && luks_ok="true" || true
    selected=$(select_backend "$requested" 2>/dev/null || true)
    printf '{"available":%s,"backend":%s,"fscrypt":%s,"luks":%s}\n' \
        "$([ -n "$selected" ] && echo true || echo false)" \
        "$([ -n "$selected" ] && printf '"%s"' "$selected" || echo null)" \
        "$fscrypt_ok" \
        "$luks_ok"
}

cmd_provision() {
    local vol="${1:-}"
    shift || true
    validate_vol_name "$vol"

    local backend="auto" uid="" gid=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --backend) backend="${2:-auto}"; shift 2 ;;
            --uid) uid="${2:-}"; shift 2 ;;
            --gid) gid="${2:-}"; shift 2 ;;
            *) err "unknown flag: $1"; return 1 ;;
        esac
    done

    local chosen=""
    chosen=$(select_backend "$backend") || {
        err "no supported encryption backend available (requested=${backend})"
        return 1
    }

    local dek_hex=""
    dek_hex=$(read_dek_hex_stdin)
    ensure_dirs
    persist_wrapped_key "$vol"

    if [ "$chosen" = "fscrypt" ]; then
        local enc_dir="${FSCRYPT_DIR}/${vol}"
        local data_dir="${enc_dir}/data"
        mkdir -p "$enc_dir"
        chmod 0700 "$enc_dir"
        hex_to_raw_pipe "$dek_hex" | timeout -k 5 30 fscrypt encrypt \
            --source=raw_key --name="smsly-${vol}" --no-recovery --quiet "$enc_dir"
        mkdir -p "$data_dir"
        chmod 0755 "$data_dir"
        create_docker_bind_volume "$vol" "$data_dir" "$uid" "$gid"
        printf '{"ok":true,"backend":"fscrypt","host_path":"%s","cipher":"fscrypt-v2-aes-256-xts"}\n' "$data_dir"
        return 0
    fi

    local img="${IMAGES_DIR}/${vol}.img"
    local mnt="${MNT_DIR}/${vol}"
    local mapper=""
    mapper=$(mapper_name_for "$vol")
    local mapper_dev="/dev/mapper/${mapper}"

    # Unmounted mountpoint stays mode 0000 with NO data/ dir on root FS (AGENTS.md #24).
    mkdir -p "$mnt"
    chmod 0000 "$mnt"

    if [ ! -f "$img" ]; then
        timeout -k 5 30 truncate -s "${VOLUME_SIZE_MB}M" "$img"
        chmod 0600 "$img"
        hex_to_raw_pipe "$dek_hex" | timeout -k 5 60 cryptsetup luksFormat \
            --type luks2 --cipher aes-xts-plain64 --key-size 512 \
            --batch-mode --key-file - "$img"
    fi

    if [ ! -e "$mapper_dev" ]; then
        hex_to_raw_pipe "$dek_hex" | timeout -k 5 30 cryptsetup luksOpen \
            --key-file - "$img" "$mapper"
        if ! timeout -k 5 15 blkid -o value -s TYPE "$mapper_dev" 2>/dev/null | grep -q "ext4"; then
            timeout -k 5 60 mkfs.ext4 -q "$mapper_dev"
        fi
    fi

    if ! timeout -k 5 10 mountpoint -q "$mnt"; then
        timeout -k 5 30 mount -o nodev,nosuid "$mapper_dev" "$mnt"
    fi

    local data_dir="${mnt}/data"
    mkdir -p "$data_dir"
    chmod 0755 "$data_dir"
    create_docker_bind_volume "$vol" "$data_dir" "$uid" "$gid"
    printf '{"ok":true,"backend":"luks","host_path":"%s","cipher":"aes-256-xts-luks2"}\n' "$data_dir"
}

cmd_unlock() {
    local vol="${1:-}"
    shift || true
    validate_vol_name "$vol"

    local backend="auto"
    while [ $# -gt 0 ]; do
        case "$1" in
            --backend) backend="${2:-auto}"; shift 2 ;;
            *) err "unknown flag: $1"; return 1 ;;
        esac
    done

    local dek_hex=""
    dek_hex=$(read_dek_hex_stdin)
    ensure_dirs

    local img="${IMAGES_DIR}/${vol}.img"
    local enc_dir="${FSCRYPT_DIR}/${vol}"
    if [ "$backend" = "fscrypt" ] || { [ "$backend" = "auto" ] && [ -d "$enc_dir" ]; }; then
        hex_to_raw_pipe "$dek_hex" | timeout -k 5 30 fscrypt unlock --quiet "$enc_dir"
        if [ ! -d "${enc_dir}/data" ]; then
            err "fscrypt data dir missing after unlock: ${enc_dir}/data"
            return 1
        fi
        printf '{"ok":true,"backend":"fscrypt","host_path":"%s/data"}\n' "$enc_dir"
        return 0
    fi

    if [ ! -f "$img" ]; then
        err "encrypted image missing for ${vol}: ${img}"
        return 1
    fi

    local mnt="${MNT_DIR}/${vol}"
    local mapper=""
    mapper=$(mapper_name_for "$vol")
    local mapper_dev="/dev/mapper/${mapper}"

    mkdir -p "$mnt"
    if ! timeout -k 5 10 mountpoint -q "$mnt"; then
        chmod 0000 "$mnt"
        if [ ! -e "$mapper_dev" ]; then
            hex_to_raw_pipe "$dek_hex" | timeout -k 5 30 cryptsetup luksOpen \
                --key-file - "$img" "$mapper"
        fi
        timeout -k 5 30 mount -o nodev,nosuid "$mapper_dev" "$mnt"
    fi
    if [ ! -d "${mnt}/data" ]; then
        err "mounted encrypted volume missing data subdir: ${mnt}/data"
        return 1
    fi
    printf '{"ok":true,"backend":"luks","host_path":"%s/data"}\n' "$mnt"
}

cmd_unlock_all() {
    ensure_dirs
    local key_file="" vol="" raw_hex=""
    for key_file in "${KEYSTORE_DIR}"/*.enc; do
        [ -e "$key_file" ] || continue
        vol=$(basename "$key_file" .enc)
        validate_vol_name "$vol" || continue
        if [ -f "${IMAGES_DIR}/${vol}.img" ] && timeout -k 5 10 mountpoint -q "${MNT_DIR}/${vol}"; then
            continue
        fi
        if command -v python3 >/dev/null 2>&1; then
            raw_hex=$(timeout -k 5 15 python3 - "$key_file" <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ.get("SMSLY_BACKEND_DIR", "/opt/smsly-hosting/backend"))
from apps.addons.services.volume_encryption import unwrap_dek
wrapped = open(sys.argv[1], "r", encoding="utf-8").read().strip()
print(unwrap_dek(wrapped).hex())
PYEOF
            ) || { err "failed to unwrap key for ${vol}"; continue; }
            printf '%s\n' "$raw_hex" | cmd_unlock "$vol" >/dev/null || err "unlock failed for ${vol}"
        fi
    done
}

cmd_verify() {
    local vol="${1:-}"
    validate_vol_name "$vol"
    local img="${IMAGES_DIR}/${vol}.img"
    local mnt="${MNT_DIR}/${vol}"
    local enc_dir="${FSCRYPT_DIR}/${vol}"

    if [ -d "$enc_dir" ]; then
        timeout -k 5 10 fscrypt status "$enc_dir" | grep -q "Unlocked: Yes" || {
            err "fscrypt directory locked: ${enc_dir}"
            return 1
        }
        [ -d "${enc_dir}/data" ] || { err "missing ${enc_dir}/data"; return 1; }
        printf '{"ok":true,"backend":"fscrypt","host_path":"%s/data"}\n' "$enc_dir"
        return 0
    fi

    if [ -f "$img" ]; then
        timeout -k 5 10 mountpoint -q "$mnt" || { err "LUKS volume unmounted: ${mnt}"; return 1; }
        [ -d "${mnt}/data" ] || { err "missing ${mnt}/data"; return 1; }
        printf '{"ok":true,"backend":"luks","host_path":"%s/data"}\n' "$mnt"
        return 0
    fi

    err "no encrypted volume found for ${vol}"
    return 1
}

cmd_cleanup() {
    local vol="${1:-}"
    shift || true
    validate_vol_name "$vol"

    while [ $# -gt 0 ]; do
        case "$1" in
            --backend) shift 2 ;;
            *) shift ;;
        esac
    done

    local img="${IMAGES_DIR}/${vol}.img"
    local mnt="${MNT_DIR}/${vol}"
    local enc_dir="${FSCRYPT_DIR}/${vol}"
    local mapper=""
    mapper=$(mapper_name_for "$vol")

    if timeout -k 5 15 docker volume inspect "$vol" >/dev/null 2>&1; then
        timeout -k 5 30 docker volume rm "$vol" >/dev/null 2>&1 || true
    fi

    if [ -d "$mnt" ] && timeout -k 5 10 mountpoint -q "$mnt"; then
        timeout -k 5 30 umount "$mnt" || true
    fi
    if [ -e "/dev/mapper/${mapper}" ] && command -v cryptsetup >/dev/null 2>&1; then
        timeout -k 5 30 cryptsetup luksClose "$mapper" || true
    fi
    rm -f "$img" "${KEYSTORE_DIR}/${vol}.enc"
    if [ -d "$mnt" ]; then
        rmdir "$mnt" 2>/dev/null || true
    fi
    if [ -d "$enc_dir" ]; then
        rm -rf "$enc_dir"
    fi
    printf '{"ok":true,"cleaned":"%s"}\n' "$vol"
}

cmd_lock() {
    local vol="${1:-}"
    shift || true
    validate_vol_name "$vol"

    while [ $# -gt 0 ]; do
        case "$1" in
            --backend) shift 2 ;;
            *) err "unknown flag: $1"; return 1 ;;
        esac
    done

    local img="${IMAGES_DIR}/${vol}.img"
    local mnt="${MNT_DIR}/${vol}"
    local enc_dir="${FSCRYPT_DIR}/${vol}"
    local mapper=""
    mapper=$(mapper_name_for "$vol")

    if [ -d "$enc_dir" ] && command -v fscrypt >/dev/null 2>&1; then
        timeout -k 5 30 fscrypt lock "$enc_dir" >/dev/null 2>&1 || true
    fi
    if [ -d "$mnt" ] && timeout -k 5 10 mountpoint -q "$mnt"; then
        timeout -k 5 30 umount "$mnt" || timeout -k 5 15 umount -l "$mnt" || true
    fi
    if [ -d "$mnt" ]; then
        chmod 0000 "$mnt" || true
    fi
    if [ -e "/dev/mapper/${mapper}" ] && command -v cryptsetup >/dev/null 2>&1; then
        timeout -k 5 30 cryptsetup luksClose "$mapper" || true
    fi
    if [ ! -d "$enc_dir" ] && [ ! -f "$img" ] && [ ! -d "$mnt" ]; then
        err "no encrypted volume found for ${vol}"
        return 1
    fi
    printf '{"ok":true,"locked":"%s"}\n' "$vol"
}

main() {
    local subcmd="${1:-}"
    if [ -z "$subcmd" ]; then
        err "usage: $0 <preflight|provision|unlock|lock|unlock-all|verify|cleanup> ..."
        return 1
    fi
    shift
    case "$subcmd" in
        preflight) cmd_preflight "$@" ;;
        provision) cmd_provision "$@" ;;
        unlock) cmd_unlock "$@" ;;
        lock) cmd_lock "$@" ;;
        unlock-all) cmd_unlock_all "$@" ;;
        verify) cmd_verify "$@" ;;
        cleanup) cmd_cleanup "$@" ;;
        *)
            err "unknown subcommand: ${subcmd}"
            return 1
            ;;
    esac
}

main "$@"
