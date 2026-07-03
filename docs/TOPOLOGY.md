# Topology Graph Builder

The `GraphBuilder` service (`backend/apps/deployments/services/graph_builder.py`) is responsible for inferring the infrastructure topology from live service definitions and environment variables.

## Overview

The graph consists of:
- **Nodes**: Services (Compute) and Addons (Database, Cache, Queue, etc).
- **Edges**:
  - `OWNS`: Relationship between a Service and its provisioned Addons.
  - `CONNECTS_TO`: Inferred network dependency based on environment variables.

## Inference Pipeline

1. **Service Registration**: All services owned by the user are scanned.
2. **Addon Mapping**: Addons linked to services are registered as nodes.
3. **Connection Inference**:
   - Environment variables are scanned for URLs (`postgres://...`, `redis://...`).
   - Keys like `DB_HOST`, `BROKER_URL`, `KAFKA_BOOTSTRAP_SERVERS` are prioritized.
   - Hostnames in values are matched against known service names and addon names.

## Extending the Graph

### Adding a New Service Subtype
Service subtypes (e.g., `postgres`, `redis`) are currently derived from the `Addon.addon_type` or `Service.deploy_type`.

To support a new addon type:
1. Update `_add_addon_node` in `GraphBuilder`.
2. Map the `addon_type` string (e.g., `CLICKHOUSE`) to a topology kind (`DATABASE`, `CACHE`, `QUEUE`, `STORAGE`, `SEARCH`).
3. Update the frontend mapping in `Topology3D.tsx` and `CanvasSchematic.tsx` if a new icon/color is needed.

### Adding New Inference Rules
To improve connection detection:
1. Modify `_infer_connections` in `GraphBuilder`.
2. Add regex patterns or specific key checks (e.g., `API_KEY` references).
3. Ensure you handle `encrypted` values carefully (decryption is handled by the model field).

## Performance Notes
- **Complexity**: The current implementation runs in roughly `O(S * E * K)` where `S` is services, `E` is avg env vars per service, and `K` is number of known services/addons.
- **Scaling**: For users with <100 services, this is negligible (<50ms).
- **Optimization**: For larger deployments, we should:
  - Cache the resulting graph in Redis with a TTL (e.g., 5 minutes) or invalidate on deployment events.
  - Use Aho-Corasick algorithm for faster substring matching of service names in env vars.
- **Frontend**: The 3D view (`react-force-graph-3d`) uses WebGL and can handle 1000+ nodes comfortably. The 2D schematic (`ReactFlow`) may degrade in performance with >500 nodes; layout calculation (`dagre`) is the bottleneck there.
