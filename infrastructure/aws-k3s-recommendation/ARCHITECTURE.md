# Grid Self-Managed Kubernetes Architecture

This document provides a comprehensive architectural recommendation for running **Grid** (and the 60+ user services it manages) on a self-managed, multi-VPS infrastructure, heavily utilizing AWS EC2 for the control plane and primary worker nodes.

## 1. Core Goal
To provide a highly scalable, self-managed orchestration foundation for a PaaS startup expecting sudden traffic spikes. The system needs to allow Grid (the control plane) to deploy and route traffic to over 60 distinct user-defined services across various VPS hosting providers over an internal network.

## 2. Orchestrator Recommendation: k3s (Lightweight Kubernetes)

While Docker Swarm is simpler, managing 60+ services with high traffic spikes across distributed nodes over a WAN (different VPS providers) strongly favors **Kubernetes**. Standard Kubernetes (k8s) is resource-heavy, so we recommend **k3s**.

**Why k3s over K8s / Swarm?**
- **Low Footprint:** Runs beautifully on smaller VPS instances, saving costs. A k3s agent (worker node) can run in less than 500MB of RAM.
- **Single Binary:** Everything (containerd, Flannel CNI, Traefik ingress, Kubelet) is packaged in a single binary, making self-managed installation incredibly easy.
- **Built for Edge/Distributed:** k3s is optimized for clusters where nodes might be on different networks or have intermittent connectivity, perfect for your multi-VPS requirement.
- **Full API Compatibility:** It is fully CNCF certified K8s. You can use any standard Helm chart or `kubectl` command.

## 3. Network Topology (Connecting the VPS Nodes)

Since your 60+ services will span "different VPS hostings", you cannot rely on AWS VPC networking alone. You need a secure **Overlay Network (Mesh VPN)** to connect all nodes into a single, seamless cluster.

### Recommendation: Tailscale (or WireGuard natively) + Flannel (k3s default CNI)
- **How it works:** Install Tailscale on every VPS (AWS EC2, DigitalOcean, Hetzner, etc.). This creates a secure, flat virtual private network (e.g., `100.x.x.x`).
- **Cluster Join:** When you join a new VPS to the k3s cluster, you use its Tailscale internal IP (`--node-ip=<tailscale-ip>` and `--flannel-iface=tailscale0`).
- **Result:** To k3s, all 60 VPS instances look like they are in the same secure local network. Pods on a node in AWS can communicate directly with pods on a node in Hetzner without traversing the public internet unencrypted.

## 4. Architectural Layers

### A. The Control Plane (Grid Core)
This should run on high-availability, reliable infrastructure (AWS EC2 is recommended).
- **Control Nodes (k3s servers):** 1 (for cost) or 3 (for High Availability) EC2 instances.
- **Stateful Backing:** PostgreSQL and Redis for Grid's internal state.
  - *Recommendation:* Run these **inside** the k3s cluster using established operators like `CloudNativePG` and `Spotahome Redis Operator`. This keeps management centralized in Kubernetes manifests. Use EBS volumes for persistence on AWS nodes.

### B. The Worker Plane (User Services)
This is where your 60+ user deployments live.
- **Worker Nodes (k3s agents):** Mix of AWS EC2 instances and your other VPS hostings.
- **Taints & Tolerations:** Use K8s taints to ensure Grid core services only run on the secure control-plane AWS nodes, while user deployments (`deployment` pods) are scheduled onto the distributed VPS worker nodes.

### C. Ingress and Routing
Grid currently uses Caddy for wildcard SSL and Traefik for internal routing.
- **K8s Native Ingress:** Replace the standalone Caddy with a Kubernetes Ingress Controller. **Traefik** comes pre-installed with k3s and is excellent for this.
- **Wildcard SSL:** Configure Traefik with `cert-manager` to handle Let's Encrypt wildcard certificates (`*.your-paas.com`) dynamically as new user services are spun up.

## 5. Integrating Grid with Kubernetes

Currently, Grid uses the Docker SDK (`docker.from_env()`) via a Docker socket proxy to spin up user containers. To migrate to k3s, the deployment pipeline (`backend/apps/deployments/services/pipeline.py` and `tasks.py`) must be adapted.

### The Strategy: "Docker-in-Docker" builder + Kubernetes API Deployer
1. **Building:** Instead of building directly on the host Docker daemon, Grid should spin up an ephemeral Kubernetes Job using a builder tool like **Kaniko** or **Buildah**. These tools build Docker images directly inside a K8s pod without needing access to a Docker socket (which is a massive security risk in a multi-tenant K8s cluster).
2. **Registry:** Kaniko pushes the built image to your internal Docker Registry (which you can also host in k3s).
3. **Deploying:** Grid's Python backend will use the `kubernetes` Python client (instead of `docker`). It will generate K8s `Deployment` and `Service` manifests for the user's app and submit them to the k3s API server.
4. **Routing:** Grid creates an `IngressRoute` (Traefik CRD) or standard `Ingress` object pointing the user's subdomain to their new K8s Service.

## 6. Traffic Spikes & Scaling

- **Pod Autoscaling:** Use the Kubernetes **Horizontal Pod Autoscaler (HPA)**. Grid can configure HPA objects for user deployments. If a user's service CPU/Memory spikes, K8s automatically spins up more pods for that specific service across your VPS nodes.
- **Node Autoscaling (AWS):** On the AWS side, use the **Cluster Autoscaler** or **Karpenter**. If you run out of capacity on your fixed VPS nodes, Karpenter can dynamically spin up new EC2 instances in seconds to handle the burst, and spin them down when traffic subsides.

## Summary Checklist for Migration
1. Provision K3s Control Plane on AWS (see Terraform).
2. Setup Tailscale Mesh VPN across all intended VPS nodes.
3. Join VPS nodes as K3s Agents.
4. Deploy CloudNativePG and Redis operators into K3s.
5. Package Grid (Django, Next.js, Celery) into a Helm Chart.
6. Refactor Grid's `pipeline.py` to use K8s API (Deployments) instead of Docker SDK.
