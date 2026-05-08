# Kubernetes Deployment Guide

> [!NOTE]
> **Version**: 1.0.1
> **Last Updated**: 2026-05-08
> **Changelog**: 
> - v1.0.1: Linked actual Helm chart directory.
> - v1.0.0: Initial K8s orchestration guide.

Grid can orchestrate deployments onto existing Kubernetes clusters.

## Prerequisites
1. A running Kubernetes cluster (K3s, GKE, EKS, AKS).
2. A `KUBECONFIG` file or a Service Account Token with namespace admin privileges.

## Helm Chart
The Grid control plane can be installed directly into your K8s cluster using our Helm chart.
You can find the charts in [charts/smsly-hosting/](file:///c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/charts/smsly-hosting/).

## Architecture
When deploying services to Kubernetes, Grid uses the `kubernetes` Python client to generate Deployments, Services, and Ingresses dynamically.
Each service gets its own Namespace or shares a project Namespace.
