# Legacy ad-hoc remediation scripts

This directory preserves one-off scripts that were used to fix SSL / custom-domain
issues during early bring-up of the platform. They are kept for historical reference
only — **do not run them in production**.

## Why they were moved here

The issues these scripts were patching (Docker daemon, Celery worker, missing
dependencies, Caddy config drift) are now handled by the core platform:

| Concern | Current authoritative location |
| --- | --- |
| Domain / Caddy drift | `scripts/fix-domain.sh` |
| SSL end-to-end smoke test | `scripts/test_custom_domain_ssl.sh` |
| Caddy config management | `backend/apps/cloud/services/caddy_manager.py` |
| Celery worker bootstrap | `backend/config/celery.py` + `docker-compose.prod.yml` |
| SSL renewal timer | `scripts/smsly-domain-ssl-manager.sh` + the matching `.service` / `.timer` units in repo root |

## Files

- `fix_custom_domain_ssl.py` — one-shot Python fix for Docker / Celery / Caddy when
  custom-domain SSL silently broke. Logic now lives in the services above.
- `quick_fix_ssl.py` — earlier, narrower version of the same remediation.
- `install-custom-domain-ssl.sh` — installer hook for the systemd unit + manager script.
  Replaced by the canonical `install.sh` flow.
- `setup-domain-ssl-complete.sh` — companion "complete" installer that bundled
  everything in one shot. Also folded into the canonical installer.

If you need to revert to one of these for a specific environment, copy the file out
and adapt it — they are not wired into any automated flow.
