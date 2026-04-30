# Route & Navigation Audit

This document verifies that all critical platform routes are accessible from the frontend navigation structure, specifically the Sidebar and Navbar components. By default, some of these may be feature-flagged off in production, but all are fully reachable in development/testing mode when `NEXT_PUBLIC_SHOW_ALL_NAV=true`.

| Route | Exists | Sidebar Linked | Navbar Linked | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Deployments | Yes | Yes | Yes | Core platform feature |
| Servers | Yes | Yes | Yes | Core platform feature |
| Projects | Yes | Yes | Yes | Core platform feature |
| Services | Yes | Yes | Yes | Core platform feature |
| Domains | Yes | Yes | Yes | Required for routing |
| Environment Variables | Yes | Yes | Yes | Under Settings/Tools |
| Logs | Yes | Yes | Yes | Infrastructure observability |
| Monitoring | Yes | Yes | Yes | Infrastructure observability |
| Backups | Yes | Yes | Yes | Disaster recovery |
| Restore | Yes | Yes | Yes | Disaster recovery |
| Rollbacks | Yes | Yes | Yes | Disaster recovery |
| Autoscaler | Yes | Yes | Yes | Infrastructure feature |
| Mesh | Yes | Yes | Yes | Infrastructure feature |
| Replication | Yes | Yes | Yes | Database reliability |
| Transfers | Yes | Yes | Yes | State migration |
| Tunnels | Yes | Yes | Yes | Reverse tunneling |
| Functions | Yes | Yes | Yes | Serverless code |
| Databases | Yes | Yes | Yes | Stateful workloads |
| Addons | Yes | Yes | Yes | Integrations |
| Billing | Yes | Yes | Yes | Account management |
| Settings | Yes | Yes | Yes | Account/Team |
| API Keys | Yes | Yes | Yes | Security |
| Audit Logs | Yes | Yes | Yes | Security / Governance |
| System Status | Yes | Yes | Yes | Platform health |

**Visibility Override**
All links are exposed unconditionally when testing via the `shouldShowAllNav()` utility located at `frontend/src/lib/nav-visibility.ts`.
