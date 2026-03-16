# AMI Lookup - Latest Ubuntu 22.04 LTS
data "aws_ami" "ubuntu" {
  most_recent = true
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  owners = ["099720109477"] # Canonical
}

# User-data scripts for provisioning K3s automatically via cloud-init.
# Note: For production, using Ansible or Packer AMIs is cleaner, but this is a solid starter.

# 1. The Control Plane Server
resource "aws_instance" "k3s_server" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.control_plane_instance_type
  key_name      = var.ssh_key_name
  subnet_id     = aws_subnet.public.id
  vpc_security_group_ids = [
    aws_security_group.k3s_control_plane.id,
    aws_security_group.k3s_workers.id # allow it to communicate internally as well
  ]

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
  }

  user_data = <<-EOF
#!/bin/bash
set -e
# Optional: Install Tailscale for Mesh VPN
if [ -n "${var.tailscale_auth_key}" ]; then
  curl -fsSL https://tailscale.com/install.sh | sh
  tailscale up --authkey=${var.tailscale_auth_key} --accept-routes
  # Use tailscale IP for K3s binding
  BIND_IP=$(tailscale ip -4)
  FLANNEL_IFACE="tailscale0"
else
  # Default to public IP binding for initial AWS setup
  BIND_IP=$(curl http://169.254.169.254/latest/meta-data/public-ipv4)
  FLANNEL_IFACE="eth0"
fi

# Install k3s as a server with Traefik enabled and Wireguard backend (better for multi-VPS)
curl -sfL https://get.k3s.io | K3S_TOKEN="${var.k3s_token}" sh -s - server \
  --node-external-ip=$BIND_IP \
  --flannel-backend=wireguard-native \
  --flannel-iface=$FLANNEL_IFACE \
  --tls-san=$BIND_IP \
  --disable=servicelb # Disable default LB, use Traefik NodePorts

echo "K3S Server Installed. To get the kubeconfig:"
echo "ssh ubuntu@$BIND_IP sudo cat /etc/rancher/k3s/k3s.yaml"
EOF

  tags = {
    Name = "${local.name_prefix}-server-1"
    Role = "control-plane"
  }
}

# 2. The Worker Nodes (AWS hosted portion)
resource "aws_instance" "k3s_worker" {
  count         = var.worker_count
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.worker_instance_type
  key_name      = var.ssh_key_name
  subnet_id     = aws_subnet.public.id
  vpc_security_group_ids = [
    aws_security_group.k3s_workers.id
  ]

  root_block_device {
    volume_size = 80 # Workers need more space for Docker images/apps
    volume_type = "gp3"
  }

  user_data = <<-EOF
#!/bin/bash
set -e
# Optional: Install Tailscale for Mesh VPN
if [ -n "${var.tailscale_auth_key}" ]; then
  curl -fsSL https://tailscale.com/install.sh | sh
  tailscale up --authkey=${var.tailscale_auth_key} --accept-routes
  # If using tailscale everywhere, you'd look up the tailscale IP of the server instead.
  # For this simple setup, we'll join via AWS public IP or Tailscale if configured.
  NODE_IP=$(tailscale ip -4)
  FLANNEL_IFACE="tailscale0"
else
  NODE_IP=$(curl http://169.254.169.254/latest/meta-data/public-ipv4)
  FLANNEL_IFACE="eth0"
fi

# The Public/Tailscale IP of the server node created above
# We pass it in dynamically.
K3S_URL="https://${aws_instance.k3s_server.public_ip}:6443"

# Install K3S as an agent
curl -sfL https://get.k3s.io | K3S_URL=$K3S_URL K3S_TOKEN="${var.k3s_token}" sh -s - agent \
  --node-external-ip=$NODE_IP \
  --flannel-iface=$FLANNEL_IFACE

# Note: The K3S installation depends on the server being fully up. In production,
# you should use Terraform `null_resource` and SSH wait-for loops to orchestrate this better.
EOF

  # Ensure the server is created before the workers try to join
  depends_on = [aws_instance.k3s_server]

  tags = {
    Name = "${local.name_prefix}-worker-${count.index + 1}"
    Role = "worker"
  }
}
