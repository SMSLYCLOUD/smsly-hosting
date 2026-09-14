#!/bin/bash
cp secrets/id_ovh_grid /tmp/ovh_grid
chmod 600 /tmp/ovh_grid
KEY="/tmp/ovh_grid"
HOST="ubuntu@139.99.218.113"
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o BatchMode=yes -i $KEY"
echo "=== REMOTE lsblk ==="
$SSH $HOST 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE'
echo ""
echo "=== REMOTE df -h ==="
$SSH $HOST 'df -h'
echo ""
echo "=== REMOTE fdisk (disks) ==="
$SSH $HOST 'lsblk -d -o NAME,SIZE,MODEL | head -10'
