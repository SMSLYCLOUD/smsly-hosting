# Ecosystem Deployment Intelligence

CloudNeuron possesses a God-level ecosystem deployment intelligence layer that allows it to safely orchestrate multiple interconnected services without requiring explicit knowledge of external service logic or VPS runtime environments.

## Core Capabilities

1. **Manifest-Driven Configuration**: Defined in `cloudneuron.ecosystem.yml`, CloudNeuron understands services, dependencies, and environment contracts before executing anything.
2. **Environment Synchronization and Persistence**: Using the `EcosystemEnvResolver`, secrets and public URLs are dynamically generated and synchronized uniformly across dependent nodes. Missing external values hard-block deployments.
3. **Primary/Control Plane Protection**: By classifying nodes using `is_control_plane` and `allow_user_workloads`, normal ecosystem services are safely routed to user-provisioned VPSs or explicit managed nodes.
4. **AI Senate Advisory Intelligence**: The coded AI Senate parses service intents to auto-complete missing environment shapes securely and explicitly through JSON-schema validated outputs.
5. **Self-Healing and Repair**: A Django management CLI (`repair_ecosystem_deploy`) permits CloudNeuron to introspect its known service database and safely assign unallocated nodes or recreate environment credentials.

## For More Information

See the targeted documentation for deeper details:
- `ECOSYSTEM_MANIFEST_SPEC.md`: Schema requirements.
- `ECOSYSTEM_DEPLOY_ORCHESTRATION_AUDIT.md`: Historical reasoning and audit flow.
- `ECOSYSTEM_ENV_FILLER.md`: Contract guarantees for secrets and variables.
- `ECOSYSTEM_DEPLOY_REPAIR_RUNBOOK.md`: Instructions for using the repair tool.
- `AI_SENATE_ENV_RESOLUTION.md`: The integration architecture of AI suggestions.

### Migration Safety & Backfill
If deploying into an existing cluster where a primary/control-plane server already hosts workloads:
1. Ensure the new migrations run (`python manage.py migrate deployments`).
2. Mark your control-plane manually (e.g. `ManagedServer.objects.filter(is_primary=True).update(is_control_plane=True, allow_user_workloads=False)`).
3. Any existing worker nodes will gracefully remain eligible for regular workloads because `is_control_plane` defaults to `False`.
