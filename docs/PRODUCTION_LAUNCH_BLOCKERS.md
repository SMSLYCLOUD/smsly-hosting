# Production Launch Blockers
- Rollback execution path still depends on background deploy task behavior and runtime health checks in deployment worker.
- Root repository contains many operational/debug scripts that need ownership triage before hard deletion.
- Add deeper automated tests for platform resource endpoint and structured errors in all service actions.
