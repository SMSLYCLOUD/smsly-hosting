#!/bin/bash
cp secrets/id_ovh_grid /tmp/ovh_grid
chmod 600 /tmp/ovh_grid
KEY="/tmp/ovh_grid"
HOST="ubuntu@176.31.201.181"
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -i $KEY"
echo "=== RESOURCES ==="
$SSH $HOST 'free -h | head -2; echo "---"; df -h | head -10; echo "---"; nproc; echo "---"; lscpu | grep "Model name" | head -1'
echo ""
echo "=== DISK DETAIL ==="
$SSH $HOST 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE | head -20; echo "---"; df -h / | tail -1'
echo ""
echo "=== NETWORK ==="
$SSH $HOST 'ip -4 addr show | grep -E "inet " | head -5; echo "---"; ip route | head -3'
echo ""
echo "=== DOCKER ==="
$SSH $HOST 'docker --version 2>&1 | head -1; echo "---"; docker compose version 2>&1 | head -1; echo "---"; docker info 2>&1 | grep -E "Server Version|Storage Driver" | head -2'
echo ""
echo "=== SMSLY CHECK ==="
$SSH $HOST 'ls -ld /opt/smsly-hosting 2>&1 | head -1; echo "---"; docker ps --format "{{.Names}}" 2>&1 | head -5; echo "---"; cat /etc/hostname; echo "---"; uptime -p'
