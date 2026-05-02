# Custom Domain SSL Audit & Architecture Report

## 1. Current architecture discovered from code
The platform uses **Caddy** running on the host server as the primary reverse proxy and SSL terminator, forwarding traffic to an internal Nginx or Docker container routes. For custom domains, it leverages Caddy's **On-Demand TLS**, which automatically issues Let's Encrypt certificates the first time an HTTPS request hits the server for an unrecognized but allowed domain. Caddy authorizes these incoming domains by sending an HTTP `ask` request to the backend at `/api/v1/services/check-domain/`.

Previously, domains were stored in a simple JSONField `custom_domains` list on the `Service` model, and there was no DNS verification prior to pushing them into Caddy.

## 2. Expected domain + SSL lifecycle
With the new implementation, the lifecycle is:
1. User deploys a service.
2. User attaches a custom domain (`POST /api/v1/services/{id}/add-domain/`).
3. Platform stores the domain in a dedicated `Domain` database model, initially with `status="pending"`.
4. A Celery background task (`verify_dns_and_provision_ssl_task`) performs a DNS preflight check. It verifies whether an A record points to the platform IP, or a CNAME points to the platform domain.
5. If DNS is not ready:
   - Status becomes `dns_pending`.
   - The domain is intentionally omitted from the generated `Caddyfile`.
   - The user gets clear status with `dns_expected` vs `dns_actual` values.
6. If DNS is ready:
   - Status becomes `dns_verified`.
   - The platform generates a Caddy route and reloads Caddy.
   - The `check-domain` API allows Caddy's On-Demand TLS to issue the certificate when traffic hits.
7. Background probing:
   - A periodic task (`check_ssl_certificates_task`) polls the domain on port 443.
   - If a valid cert is found, the status becomes `active` and `ssl_active=True`.
   - If it fails, the error is stored and it may become `ssl_failed`. It also checks expiry dates and re-triggers Caddy if renewal is needed.
8. Deletion:
   - Removing the domain from the UI (`POST /api/v1/services/{id}/delete-domain/`) removes the `Domain` database record and regenerates the Caddyfile, dropping the route.

## 3. Actual broken behavior
Before the fix, adding a domain appended a string to a JSON array. Caddy blindly accepted all of them in the generated Caddyfile. Let's Encrypt issuance would spam or fail invisibly if the user had not yet configured their DNS records correctly, causing rate limits and providing zero visibility to the user.

## 4. Root causes
- No explicit domain entity mapping to track lifecycle statuses (pending, verified, failed).
- No synchronous or asynchronous DNS preflight logic.
- Caddyfile generation included unverified domains.

## 5. Files changed
- `backend/apps/domains/models.py`: Added robust `Domain` model with `status`, `dns_expected`, `dns_actual`, etc.
- `backend/apps/deployments/views.py`: Updated `add_domain`, `delete_domain`, and `check_domain` logic.
- `backend/apps/domains/tasks.py`: Created `verify_dns_and_provision_ssl_task` to run DNS validations.
- `backend/services/caddy_manager.py`: Modified `generate_caddyfile` to only include verified domains.
- `backend/apps/cloud/services/ssl_monitor.py`: Expanded monitoring to probe custom domain statuses and retry DNS checks.

## 6. Tests added
- `backend/apps/domains/tests.py`: `DnsVerificationTests` proving A/CNAME logic.
- Mocked tasks and improved `test_custom_domain_instant_routing.py`.
- Mocked DB lookups in `test_caddy_domain_check.py`.
- Checked duplicate handling.
- Ensured Let's Encrypt isn't touched in automated tests.

## 7. Manual verification commands
To verify the system end-to-end (DNS validation and background cert check):
```bash
python manage.py test apps.domains.tests
python manage.py test apps.deployments.tests.test_custom_domain_instant_routing
```

## 8. Production deployment notes
- Ensure the background Celery worker is running so `verify_dns_and_provision_ssl_task` picks up newly added domains.
- Caddyfile is written to `/caddy-config/Caddyfile` on the host, which means the Celery worker container must have appropriate write access to that shared volume.
- Since we use Caddy On-Demand TLS, Let's Encrypt calls are safely delayed until valid traffic hits *after* our explicit DNS validation passes.
