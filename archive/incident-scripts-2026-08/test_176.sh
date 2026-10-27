#!/bin/bash
cp secrets/id_ovh_grid /tmp/ovh_grid
chmod 600 /tmp/ovh_grid
KEY="/tmp/ovh_grid"
HOST="ubuntu@176.31.201.181"
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -i $KEY"
echo "=== TRY $HOST with /tmp/ovh_grid ==="
timeout 14 $SSH $HOST 'echo SSH_OK-$(hostname); echo "---"; uptime | cut -d, -f1; echo "---"; lsb_release -d 2>/dev/null | head -1; cat /etc/os-release | grep PRETTY_NAME; echo "---KERNEL---"; uname -r; echo "---ARCH---"; uname -m' 2>&1
EC=$?
echo "exit=$EC"
if [ $EC -ne 0 ]; then
  echo "--- fallback: try default keys ---"
  for k in ~/.ssh/id_ed25519 ~/.ssh/id_rsa; do
    [ -f "$k" ] && echo "trying $k" && timeout 8 ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=6 -o BatchMode=yes -i "$k" $HOST 'echo OK-$(hostname)' 2>&1 | head -2
  done
  echo "--- debug pubkey offer ---"
  timeout 10 ssh -v -o StrictHostKeyChecking=accept-new -o ConnectTimeout=6 -o BatchMode=yes -i $KEY $HOST 'exit 0' 2>&1 | grep -i "Offering public key\|Authenticat\|Permission denied" | head -6
fi
