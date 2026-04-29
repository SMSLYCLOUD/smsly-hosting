# Deployment Safety

This document outlines the safety checks and production readiness logic for deployments and auto-approval workflows.

## Auto-Redeploy and Webhooks

When a webhook (e.g. from GitHub) triggers a push event to a tracked branch, the platform will only automatically bypass the manual review phase if the service explicitly has `can_auto_deploy` enabled.
- For standard services: Auto-redeploy is blocked if `can_auto_deploy=False`. The new deployment is placed in the QUEUED/REVIEW state until a user approves it.
- For preview environments: Preview deployments inherit their `can_auto_deploy` permission from their parent service.
- Warning: Auto-approval should not be enabled for services containing destructive database migrations or major runtime changes without caution.

## Disaster Recovery and Failure Isolation

Deployments are executed in an isolated staging pipeline before traffic is shifted:
- **Build Failures:** If a deployment fails to build, the current active container remains serving traffic. The failure is logged and isolated.
- **Health Check / Promotion Failures:** During atomic blue-green promotion (`promote_container`), the green container is verified for health. If it fails, the previous active container is untouched and the promotion aborts.
