#!/bin/bash
# =============================================================================
# Grid Installer Health Diagnostic
# Verifies the readiness of the host for SMSLY Grid installation/updates.
# =============================================================================

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}SMSLY Grid Installation Diagnostic v1.0${NC}"
echo -e "------------------------------------------------"

# 1. OS & Kernel
echo -n "Checking OS... "
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${GREEN}$NAME $VERSION_ID${NC}"
else
    echo -e "${RED}Unknown OS${NC}"
fi

# 2. Hardware Pre-flight
echo -n "Checking RAM... "
RAM_TOTAL=$(free -m | awk '/^Mem:/{print $2}')
if [ "$RAM_TOTAL" -ge 950 ]; then
    echo -e "${GREEN}${RAM_TOTAL}MB OK${NC}"
else
    echo -e "${RED}${RAM_TOTAL}MB (Min 1GB required)${NC}"
fi

echo -n "Checking Disk Space (/opt)... "
DISK_AVAIL=$(df -m /opt 2>/dev/null | tail -1 | awk '{print $4}')
if [ -z "$DISK_AVAIL" ]; then DISK_AVAIL=$(df -m / | tail -1 | awk '{print $4}'); fi

if [ "$DISK_AVAIL" -ge 1500 ]; then
    echo -e "${GREEN}${DISK_AVAIL}MB OK${NC}"
else
    echo -e "${RED}${DISK_AVAIL}MB (Min 1.5GB required)${NC}"
fi

# 3. Connectivity
echo -n "Checking Internet... "
if curl -Is --connect-timeout -k 5 5 https://google.com >/dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${RED}FAILED (google.com)${NC}"
fi

echo -n "Checking GitHub Access... "
if curl -Is --connect-timeout -k 5 5 https://github.com >/dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${RED}FAILED (github.com)${NC}"
fi

# 4. Critical Dependencies
echo -e "\n${BLUE}Dependency Check:${NC}"
for CMD in docker git python3 curl host; do
    echo -n "  $CMD: "
    if command -v "$CMD" >/dev/null 2>&1; then
        VER=$($CMD --version 2>/dev/null | head -n 1 || $CMD -v 2>/dev/null)
        echo -e "${GREEN}OK ($VER)${NC}"
    else
        echo -e "${RED}MISSING${NC}"
    fi
done

# 5. Installer State
echo -e "\n${BLUE}Installer State:${NC}"
if [ -f "/opt/smsly-hosting/.smsly_install_state" ]; then
    echo -e "  State file found. Last checkpoints:"
    tail -n 5 "/opt/smsly-hosting/.smsly_install_state" | sed 's/^/    - /'
else
    echo -e "  ${YELLOW}No installation state found (Fresh VPS)${NC}"
fi

if [ -f "/tmp/smsly-install.lock" ]; then
    echo -e "  ${YELLOW}WARNING: Installer lock file present (PID $(cat /tmp/smsly-install.lock))${NC}"
fi

echo -e "\n------------------------------------------------"
echo -e "${BLUE}Diagnostic Complete.${NC}"
