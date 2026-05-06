# Global Deployment Guide

Grid is designed to run anywhere.

## 1. Kubernetes (Any Cloud)
Use our Helm Chart to deploy the control plane to EKS, GKE, AKS, or DigitalOcean K8s.

```bash
helm repo add smsly https://charts.smsly.cloud
helm install my-paas smsly/smsly-hosting
```

## 2. Low-Latency Regions
We optimize for "Edge" deployment.
- **Africa**: Deploy to AWS `af-south-1` or Azure `southafrica-north`.
- **Asia**: AWS `ap-southeast-1` (Singapore) or `ap-northeast-1` (Tokyo).
- **South America**: AWS `sa-east-1` (São Paulo).

## 3. Configuration
Set `GLOBAL_REGION_PREF` in your `.env` to default your users to the closest data center.

```env
GLOBAL_REGION_PREF=af-south-1
```
