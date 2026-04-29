# Cost Estimation
Estimated monthly cost is computed per service in serializer:
`node_monthly_cost * (service_ram_mb / node_ram_mb)` with clamped minimum weight.

Env vars:
- `PLATFORM_COST_ESTIMATION_ENABLED`
- `PLATFORM_COST_CURRENCY`
- `PLATFORM_DEFAULT_NODE_MONTHLY_COST`
- `PLATFORM_DEFAULT_NODE_RAM_MB`
