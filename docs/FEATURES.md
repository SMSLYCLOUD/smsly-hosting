# God-Mode Features

## 1. Hot Functions (Serverless)
SMSLY creates "Serverless" behavior on standard Docker/K8s containers.
- **Mechanism**: We mount your source code into a pre-warmed runtime container (e.g., `python:3.9-slim`).
- **Dynamic Entrypoint**: A lightweight Python/Node script wraps your handler function (e.g., `handler(event, context)`) and exposes it via HTTP on port 8080.
- **Benefit**: No need for AWS Lambda or proprietary vendor lock-in.

## 2. Ephemeral Preview Environments
Every Pull Request gets a full stack environment.
- **Trigger**: GitHub Webhook (`pull_request` event).
- **Action**:
  1. Clone Production Service config.
  2. Override Branch/Commit.
  3. Deploy to new subdomain (`app-pr-123.smsly.cloud`).
  4. Post URL back to GitHub PR comment (Roadmap item).
- **Cleanup**: Closing the PR destroys the resources.

## 3. AI Anomaly Detection
We don't just graph CPU usage; we analyze it.
- **Statistical Model**: Uses Z-Score (Standard Score) to identify data points > 3 standard deviations from the mean.
- **LLM Integration**: If configured, sends the anomaly context to Gemini/OpenAI for a plain-English explanation.

## 4. Database Auto-Healing
Uses the **Patroni** architecture.
- **Etcd**: Distributed Key-Value store for consensus.
- **Patroni**: Manages PostgreSQL replication and leader election.
- **HAProxy**: Routes traffic to the current leader.
- **Result**: If the Primary DB node dies, a Replica promotes itself within seconds.
