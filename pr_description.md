### Overview
Upgrades CloudNeuron’s ecosystem deployment intelligence to reliably orchestrate complex interconnected services via strict manifests and backend validation.

### Final PR Readiness Report
- **Migration Status**: Clean graph running natively up to `0056`. Faulty historical manual migration was safely pruned in favor of the correct Django auto-generated chain.
- **Commands/Tests Run**: `manage.py test` passed for both old code logic and specific ecosystem logic `manage.py test apps.deployments.tests.test_ecosystem_...`
- **Focused Deployment Tests Result**: Full pass. Ecosystem simulation generic unit tests pass, blocking cleanly if required external variables are missing or if unassigned nodes are simulated.
- **Docs Added**: `ECOSYSTEM_MANIFEST_SPEC.md`, `AI_SENATE_ENV_RESOLUTION.md`, `ECOSYSTEM_ENV_FILLER.md`, `ECOSYSTEM_DEPLOY_REPAIR_RUNBOOK.md`, `ECOSYSTEM_DEPLOY_ORCHESTRATION_AUDIT.md`, `ECOSYSTEM_DEPLOYMENT_INTELLIGENCE.md`.
- **Known Limitations**: CloudNeuron explicitly operates on contract-driven evidence in its own DB. It does NOT assume access to remote VPS logs or external service repos by design.
- **UI Diagnostics**: Replaced generic "FAILED" cards with distinct missing node and diagnostic strings in `ServicesGrid.tsx` and `app/deployments/[id]/page.tsx`. Secrets remain redacted securely.
