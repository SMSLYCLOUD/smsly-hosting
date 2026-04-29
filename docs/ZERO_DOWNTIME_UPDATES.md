# Zero-Downtime Updates

## Platform Updater (Self-Update)
The CloudNeuron platform orchestrates zero-downtime self-updates:
- A snapshot is taken of the current running containers.
- Code is fetched and images are built without touching live traffic.
- Containers are restarted sequentially in dependency order (`db` -> `redis` -> ... -> `frontend` -> `nginx`).
- **Safety Gate:** If a critical service (`db`, `redis`, `pgbouncer`) fails its health check after restart, a `PlatformUpdateError` is raised, stopping the update and triggering an automated rollback.
- Concurrent updates are strictly locked.
