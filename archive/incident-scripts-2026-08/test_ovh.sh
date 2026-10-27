#!/bin/bash
set -u
KEY="secrets/id_ovh_grid"
HOST="ubuntu@139.99.218.113"
echo "=== testing $HOST with $KEY ==="
timeout 18 ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -i "$KEY" "$HOST" 'echo "SSH_OK $(hostname)"; echo "---"; uptime | cut -d, -f1; echo "---"; lsb_release -d 2>/dev/null | head -1; echo "---MEM---"; free -h | awk "NR==2{print \$2, \$3, \$7}"; echo "---DISK---"; df -h / | tail -1; echo "---DOCKER---"; docker --version 2>&1 | head -1; echo "---KERNEL---"; uname -r' 2>&1
EC=$?
echo "exit=$EC"
if [ $EC -ne 0 ]; then
  echo "---fallback: try without -i (agent/default keys) ---"
  timeout 12 ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o BatchMode=yes "$HOST" 'echo FALLBACK_OK' 2>&1 | head -5
fi
