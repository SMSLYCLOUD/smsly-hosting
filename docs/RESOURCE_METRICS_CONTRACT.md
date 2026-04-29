# Resource Metrics Contract
Endpoint: `GET /api/v1/platform/resources/`

Returns:
- `nodes[]`: cpu, memory, disk, uptime, container counts, warnings.
- `summary`: aggregate node, RAM, disk utilization.

Implementation: `PlatformResourcesView` in deployments views.
