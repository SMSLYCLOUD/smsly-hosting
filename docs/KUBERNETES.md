# Kubernetes Deployment Guide

CloudNeuron can orchestrate deployments onto existing Kubernetes clusters.

## Prerequisites
1. A running Kubernetes cluster (K3s, GKE, EKS, AKS).
2. A `KUBECONFIG` file or a Service Account Token with namespace admin privileges.

## Helm Chart (Coming Soon)
We are actively developing a Helm chart to install the CloudNeuron control plane directly into your K8s cluster.
You can find the scaffold in `infrastructure/helm/`.

## Architecture
When deploying services to Kubernetes, CloudNeuron uses the `kubernetes` Python client to generate Deployments, Services, and Ingresses dynamically.
Each service gets its own Namespace or shares a project Namespace.
