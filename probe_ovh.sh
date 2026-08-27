#!/bin/bash
KEY="/tmp/ovh_grid"
HOST="ubuntu@139.99.218.113"
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -i $KEY"
echo "=== HOST ==="
$SSH $HOST 'hostname -f; echo "---"; uptime; echo "---"; lsb_release -d 2>/dev/null | head -1; cat /etc/os-release | grep PRETTY_NAME; echo "---KERNEL---"; uname -r; echo "---ARCH---"; uname -m'
echo ""
echo "=== RESOURCES ==="
$SSH $HOST 'free -h | head -2; echo "---"; df -h / | tail -1; echo "---"; nproc; echo "---"; lscpu | grep "Model name" | head -1'
echo ""
echo "=== NETWORK ==="
$SSH $HOST 'ip -4 addr show | grep -E "inet " | head -5; echo "---"; ip route | head -3'
echo ""
echo "=== DOCKER ==="
$SSH $HOST 'docker --version 2>&1 | head -1; echo "---"; docker compose version 2>&1 | head -1; echo "---"; docker info 2>&1 | grep -E "Server Version|Storage Driver" | head -2'
echo ""
echo "=== SMSLY CHECK ==="
$SSH $HOST 'ls -ld /opt/smsly-hosting 2>&1 | head -1; echo "---"; docker ps --format "{{.Names}}" 2>&1 | head -5'
