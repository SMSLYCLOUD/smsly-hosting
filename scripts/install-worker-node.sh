#!/bin/bash
set -e

# =============================================================================
#   SMSly Hosting - Worker Node Installer (K3s Lightweight Kubernetes)
#   Version: 2.0.0
#   Features: K3s, Helm, Cert-Manager, Metrics Server
# =============================================================================

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}   SMSly Hosting - Worker Node Installer (K3s)        ${NC}"
echo -e "${BLUE}======================================================${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root (sudo ./install-worker-node.sh)${NC}"
  exit 1
fi

# Check minimum requirements
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_RAM" -lt 3500 ]; then
  echo -e "${YELLOW}Warning: Minimum 4GB RAM recommended for worker nodes. You have ${TOTAL_RAM}MB${NC}"
  read -p "Continue anyway? (y/N): " CONTINUE
  [ "$CONTINUE" != "y" ] && exit 1
fi

# Get Control Plane info
echo -e "${BLUE}--- Configuration ---${NC}"
read -p "Control Plane Domain (e.g. hosting.smsly.cloud): " CONTROL_PLANE_DOMAIN
read -p "Worker Node Name (e.g. worker-01): " NODE_NAME
NODE_NAME=${NODE_NAME:-"worker-$(hostname)"}

# 1. System Hardening
echo -e "${GREEN}[+] Configuring Firewall...${NC}"
apt-get update && apt-get install -y ufw fail2ban
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
# K3s ports
ufw allow 6443/tcp    # Kubernetes API
ufw allow 10250/tcp   # Kubelet
ufw allow 10251/tcp   # Scheduler
ufw allow 10252/tcp   # Controller
ufw allow 8472/udp    # Flannel VXLAN
ufw allow 51820/udp   # Wireguard (optional)
ufw --force enable

systemctl enable fail2ban
systemctl start fail2ban

# 2. Install K3s (Lightweight Kubernetes)
echo -e "${GREEN}[+] Installing K3s...${NC}"
curl -sfL https://get.k3s.io | sh -s - \
    --node-name "$NODE_NAME" \
    --write-kubeconfig-mode 644

# Wait for K3s to be ready
echo -e "${GREEN}[+] Waiting for K3s to be ready...${NC}"
sleep 10
kubectl wait --for=condition=Ready nodes --all --timeout=120s

# 3. Extract Kubeconfig
echo -e "${GREEN}[+] Extracting Kubeconfig...${NC}"
mkdir -p ~/.kube
cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
chmod 600 ~/.kube/config

# Get Public IP
PUBLIC_IP=$(curl -s ifconfig.me || curl -s icanhazip.com)
# Replace localhost with public IP for remote access
sed -i "s/127.0.0.1/$PUBLIC_IP/g" ~/.kube/config

# 4. Install Helm
echo -e "${GREEN}[+] Installing Helm...${NC}"
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# 5. Install Cert-Manager (for SSL)
echo -e "${GREEN}[+] Installing Cert-Manager...${NC}"
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install \
  cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.14.0 \
  --set installCRDs=true

# 6. Install Metrics Server (for HPA)
echo -e "${GREEN}[+] Installing Metrics Server...${NC}"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# For local/self-signed clusters, patch metrics-server
kubectl patch deployment metrics-server -n kube-system \
  --type='json' \
  -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]' || true

# 7. Create namespace for user workloads
echo -e "${GREEN}[+] Creating smsly-workloads namespace...${NC}"
kubectl create namespace smsly-workloads || true

# Apply resource quotas
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ResourceQuota
metadata:
  name: default-quota
  namespace: smsly-workloads
spec:
  hard:
    requests.cpu: "8"
    requests.memory: 16Gi
    limits.cpu: "16"
    limits.memory: 32Gi
    pods: "50"
EOF

# 8. Display Kubeconfig
echo ""
echo -e "${BLUE}======================================================${NC}"
echo -e "${YELLOW}IMPORTANT: Copy this kubeconfig to your Control Plane${NC}"
echo -e "${BLUE}======================================================${NC}"
echo ""
cat ~/.kube/config
echo ""
echo -e "${BLUE}======================================================${NC}"
echo ""
echo -e "${GREEN}Instructions:${NC}"
echo "1. Copy the above kubeconfig content"
echo "2. On the Control Plane server, save it to: /opt/smsly-hosting/kubeconfig"
echo "3. Update docker-compose.yml to mount it:"
echo ""
echo "   volumes:"
echo "     - ./kubeconfig:/root/.kube/config:ro"
echo ""
echo "4. Restart services: docker compose restart backend celery"
echo ""
echo -e "${BLUE}======================================================${NC}"
echo -e "${GREEN}   Worker Node Ready: ${NODE_NAME} (${PUBLIC_IP})${NC}"
echo -e "${BLUE}======================================================${NC}"
