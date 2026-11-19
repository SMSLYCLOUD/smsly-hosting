# Trulay Grid security gap review — 2026-11-17

## Scope and evidence
Review of current local smsly-hosting working tree, including prior uncommitted remediation. Source-traced findings, focused isolated Django tests, no live penetration testing. This is a bounded review, not a complete vulnerability certification. Earlier 60-test result predates the two additional fixes below.

## 1. Critical: global recovery phrase could be replaced by an ordinary user — fixed locally
Generation wrote the singleton PlatformConfig recovery hash behind IsAuthenticated; verification accepts that phrase and a chosen superuser username, bypassing MFA by design. Thus an ordinary authenticated account could generate a phrase and use it to recover an administrator. Restricted read API tokens also reached the former GET generator. Evidence: backend/apps/core/views/recovery.py generation and verification. Local fix requires full account credential plus superuser generation, and active superusers at recovery login. Focused credential suite: 20 passed reported by implementer after the superuser/active-user fix; earlier 17 independently executed by lead before that additional restriction. All changed Python modules compile and diff whitespace checks pass. The generator still changes credentials via GET; migrate to POST with CSRF protection and update clients as a follow-up.

## 2. High: ordinary authenticated users could download full server backups — FIXED LOCALLY
The source defect was at backend/apps/deployments/views/server_backup.py:205-219, where download overrode class IsAdminUser with IsAuthenticated and directly fetched any backup UUID. Local patch now requires admin authorization for unsigned download and download-key actions; signed query links no longer bypass the admin gate. Py_compile passed; dedicated runtime tests were blocked by missing rest_framework_nested during collection.

## 3. Medium: anonymous route recheck clears tenant restart protection — fixed locally, tests pending final confirmation
backend/apps/deployments/views/route.py allowed anonymous GET/POST for known public domains and invoked reset_restart_state before a health check. health_monitor.py:140-147 deletes failure counters, restart caps/backoff, last-check time and alert cooldown. Repeated calls can suppress failure accumulation or reenable exhausted restart budgets; serial calls do not directly guarantee an immediate restart. Local fix returns stored health_status without invoking the mutation/probe helper. Focused route security tests now pass: 2 passed. Existing full APIClient route tests remain environment-sensitive because the isolated runner does not include every production middleware dependency.

## Remaining gaps / not proven vulnerabilities
- Compose/Nixpacks have separate build paths beyond the recently contained Docker dispatcher. Full merged Compose validation for host mounts, capabilities, external networks and effective resource limits needs a dedicated review; no exploitable host escape is claimed here.
- Public route throttling trusts first X-Forwarded-For and uses non-atomic get/set; read-only change removes restart-state impact but rate-limit robustness remains.
- Global recovery generation remains GET and phrase is platform-wide, reusable; require POST and define rotation/invalidation policy.
- Real PostgreSQL locking, Redis distributed replay exclusion, Docker daemon isolation and backup restore must be verified in isolated Linux staging.
- Source findings do not establish live deployment exposure or exploitation.
