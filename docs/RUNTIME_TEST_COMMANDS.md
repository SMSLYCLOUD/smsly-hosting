# Runtime Test Commands

## Direct backend (control-plane accurate)
- `curl -i http://127.0.0.1:8000/api/v1/health/`
- `curl -i http://backend:8000/api/v1/health/` (from compose network shell)

## Public route checks (ingress/proxy path)
- `curl -i http://127.0.0.1:8081/api/v1/health/`

If HTML fallback is returned instead of JSON, the request hit fallback/edge routing rather than backend API.

## Docker/runtime checks
- `docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"`
- `docker ps --filter "name=green" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"`
- `docker logs --tail=200 smsly-hosting-backend-1`
- `docker logs --tail=200 smsly-hosting-celery-deploy-1`

## Feature endpoint probes
- Replication health: `curl -i http://127.0.0.1:8000/api/v1/replication/health/<mesh_id>/`
- Mesh health: `curl -i http://127.0.0.1:8000/api/v1/mesh/<mesh_id>/health/`
- Transfer create validation: `curl -i -X POST http://127.0.0.1:8000/api/v1/transfers/ -H 'Content-Type: application/json' -d '{}'`
- Tunnel list/health: `curl -i http://127.0.0.1:8000/api/v1/tunnels/`
- Backups: `curl -i http://127.0.0.1:8000/api/v1/server/backups/`
- Autoscaler: `curl -i http://127.0.0.1:8000/api/v1/autoscaler/status/`
