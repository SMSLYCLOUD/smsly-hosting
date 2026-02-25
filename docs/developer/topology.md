# Topology & Graph Builder Documentation

## Overview

The Topology feature provides a visualization of the deployed infrastructure, including services, addons, and their dependencies.
The system automatically infers connections between services by analyzing environment variables and resource ownership.

## Architecture

1.  **Backend (`GraphBuilder`)**:
    *   Located in `backend/apps/deployments/services/graph_builder.py`.
    *   Fetches all `Service` and `Addon` records for a user.
    *   Scans `EnvironmentVariable` values for connection strings (URLs) or hostnames.
    *   Builds a graph with Nodes (Services, Addons) and Edges (Ownership, Connections).
    *   Exposed via API `GET /api/v1/topology/`.

2.  **Frontend**:
    *   Located in `frontend/src/app/topology/page.tsx` (Page) and `frontend/src/components/topology/` (Views).
    *   **3D View (`Topology3D`)**: Force-directed graph using `react-force-graph-3d`.
    *   **Schematic View (`CanvasSchematic`)**: 2D railway-style diagram using `reactflow` and `dagre` layout.
    *   **Solar System View (`SolarSystemView`)**: Orbit visualization using `three.js`.
    *   **Side Panel (`ServiceSidePanel`)**: Shared component for viewing node details and revealing secrets.

## Extending the Graph

### Adding a New Node Type

1.  Update `backend/apps/deployments/services/graph_builder.py`:
    *   Modify `_add_service_node` or `_add_addon_node` to handle the new type.
    *   Assign a `kind` (e.g., `COMPUTE`, `DATABASE`, `CACHE`) and `subtype`.

2.  Update Frontend Types (`frontend/src/types/topology.ts`):
    *   Add the new kind to `TopologyNodeData['kind']`.

3.  Update Visualizations:
    *   **3D**: Add a geometry case in `Topology3D.tsx` -> `nodeThreeObject`.
    *   **2D**: Add an icon mapping in `CanvasSchematic.tsx` -> `CustomNode`.
    *   **Solar**: Add a planet color/size rule in `SolarSystemView.tsx`.

### Adding Connection Inference Rules

To support new ways of connecting services (e.g., a new env var pattern):

1.  Edit `backend/apps/deployments/services/graph_builder.py`.
2.  Update `_infer_connections` method.
3.  Add regex or heuristic logic to parse `env.value`.
4.  Call `self._match_and_link(service, target_host, protocol, evidence_key)`.

## Environment Variables & Secrets

*   Environment variables are stored encrypted in the database.
*   The Topology API returns metadata but **does not** return secret values.
*   Secrets are revealed on-demand via `POST /api/v1/services/{id}/env_vars/reveal/`, which requires authentication and ownership.
*   The UI Side Panel handles masking and revealing.

## Performance

*   The `GraphBuilder` performs $O(N \cdot M)$ checks where $N$ is services and $M$ is env vars. For typical usage (< 500 services), this is negligible.
*   Frontend `react-force-graph-3d` can handle 1000+ nodes.
*   `SolarSystemView` uses efficient Three.js instancing/mesh reuse patterns where possible, but currently uses individual Meshes for simplicity.
