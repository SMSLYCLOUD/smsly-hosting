# Trulay Grid - Core Features

## 1. Hot Functions (Serverless)
Trulay Grid creates "serverless" behavior on standard Docker/Kubernetes containers.
- **Mechanism**: We mount your source code into a runtime container (e.g., `node:18-alpine` or `python:3.9-slim`).
- **Dynamic Entrypoint**: A lightweight Python/Node script wraps your handler function and exposes it via HTTP on port 8000.
- **Handler Signatures**: Node.js supports `fn(event)` or `fn(request, response)`. Python supports `handler(event)` or `handler(FunctionRequest)`.
- **Benefit**: No need for AWS Lambda or proprietary vendor lock-in.

## 2. Ephemeral Preview Environments
Every Pull Request gets a full stack environment.
- **Trigger**: GitHub Webhook (`pull_request` event — opened, reopened, synchronize, closed).
- **Action**:
  1. Clone parent Service config (repository, build command, ports, resources).
  2. Override Branch/Commit.
  3. Deploy to preview subdomain (`{branch-slug}-{app-name}-{suffix}-preview.{base_domain}`).
  4. Post URL back to GitHub PR comment (Roadmap item).
- **Cleanup**: Closing the PR destroys the preview and its containers.
- **Extras**: Database clones, migration validation, and deployment approvals are also supported.

## 3. AI Anomaly Detection
Multi-layer runtime analysis with auto-remediation.
- **Log Pattern Scanning**: Regex-based `LogAnalyzer` scans build logs every 180s for 13 failure patterns (OOM, crash loops, SSL errors, port conflicts, etc.).
- **Statistical Model**: Z-Score implementation exists for metric-based anomaly detection (3-sigma threshold).
- **Multi-Provider LLM Integration**: If configured, sends anomaly context to any of 10+ providers (OpenAI, Gemini, Claude, DeepSeek, Mistral, NVIDIA NIM, Cloudflare Workers AI, Groq, Alibaba DashScope, OpenRouter, Ollama) for a plain-English explanation.
- **Auto-Remediation**: `RemediationEngine` can automatically scale up, rollback, rebuild, or restart services based on detected anomalies.

## 4. Database Auto-Healing
Uses the **Patroni** architecture for production-grade PostgreSQL HA.
- **Etcd**: Distributed Key-Value store for consensus (`quay.io/coreos/etcd:v3.5.9`).
- **Patroni**: Manages PostgreSQL replication and leader election (`ghcr.io/zalando/spilo-16:3.3-p3`).
- **HAProxy**: Routes traffic to the current leader (`haproxy:2.8`).
- **Result**: If the Primary DB node dies, Patroni automatically promotes a replica. Health checks run every 30s with a 180s readiness timeout.
- **Extras**: Manual failover support, replication lag monitoring (1MB warning, 10MB critical), and scale-out to add new nodes.
