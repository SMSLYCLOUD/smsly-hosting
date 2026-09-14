#!/bin/bash
set -u
cp secrets/id_ovh_grid /tmp/ovh_grid
chmod 600 /tmp/ovh_grid
ls -l /tmp/ovh_grid
echo "---try /tmp key---"
timeout 14 ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o BatchMode=yes -i /tmp/ovh_grid ubuntu@139.99.218.113 'echo OK-$(hostname)' 2>&1 | head -5
echo "exit1:$?"
echo "---try default keys---"
ls ~/.ssh/id_* 2>&1 | head -5
for k in ~/.ssh/id_ed25519 ~/.ssh/id_rsa; do
  if [ -f "$k" ]; then
    echo "trying $k"
    timeout 8 ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=6 -o BatchMode=yes -i "$k" ubuntu@139.99.218.113 'echo OK-$(hostname)' 2>&1 | head -2
  fi
done
echo "---ssh -v pubkey line via /tmp key (debug)---"
timeout 10 ssh -v -o StrictHostKeyChecking=accept-new -o ConnectTimeout=6 -o BatchMode=yes -i /tmp/ovh_grid ubuntu@139.99.218.113 'exit 0' 2>&1 | grep -i "Offering public key\|Authenticat\|Permission denied" | head -6
