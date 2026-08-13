# Brand and Naming

## Brand Architecture

- **Company:** Trulay
- **Product:** Trulay Grid
- **Compact UI label:** Grid
- **Positioning:** The trust layer for internet communications
- **Corporate domain:** `trulay.co`
- **Intended hosted control-plane domain:** `grid.trulay.co`

Use **Trulay Grid** on first mention in documentation, onboarding, authentication, billing, legal pages, release announcements, and other identity-bearing surfaces. Use **Grid** where the product context is already clear or space is constrained.

## Domain Transition

The intended canonical hosted control-plane domain is `grid.trulay.co`. Existing installations and workload URLs under `grid.smsly.cloud` remain valid during migration. Do not remove or redirect workload hostnames until OAuth callbacks, webhooks, DNS records, certificates, API clients, and customer integrations have been audited.

Self-hosted documentation should normally use operator-owned examples such as `grid.example.com`, not Trulay-operated production domains.

## Compatibility Identifiers

The rebrand does not automatically rename technical contracts. Keep the following identifiers until a dedicated compatibility migration is designed and shipped:

- GitHub organization and repository URLs under `SMSLYCLOUD`
- Repository and installation directory names such as `smsly-hosting`
- Environment variables such as `SMSLY_*`
- CLI package, executable, and configuration names such as `smsly`
- Docker containers, networks, volumes, labels, and image names using `smsly-*`
- Python modules, Celery task names, API provider IDs, database tables, and persisted values
- Existing customer domains, OAuth callbacks, webhook targets, certificates, and API endpoints

These values may be externally consumed or persisted. Renaming them requires compatibility aliases, data migrations, rollout sequencing, and rollback coverage.

## Writing Rules

- Write **Trulay**, never `TruLay`, `TRULAY`, or `Trulay Cloud` unless quoting a historical artifact.
- Write **Trulay Grid** on first product mention; use **Grid** afterward when unambiguous.
- Do not describe Grid as a legacy code name.
- Do not replace strings inside commands, environment variables, paths, URLs, image names, task names, or code examples unless the underlying implementation has also migrated.
- Keep historical changelog entries accurate. Add current naming context instead of rewriting release history.
