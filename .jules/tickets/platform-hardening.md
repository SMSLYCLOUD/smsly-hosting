# Platform Hardening: Production Readiness Gaps

## Context
Catch-all ticket for remaining production readiness items not covered by other tickets. These are the final gaps between "works" and "production-grade SaaS".

## Items

### 1. Rate Limiting & Abuse Prevention
File: `backend/apps/core/middleware/ratelimit.py` [VERIFY/MODIFY]

- [ ] Verify rate limits on all endpoints (deploy, query, AI)
- [ ] Per-user rate limits tied to plan tier
- [ ] Brute force protection on auth endpoints
- [ ] Webhook endpoint abuse prevention (verify signatures)

### 2. SSL Certificate Auto-Renewal Monitoring
File: `backend/apps/cloud/services/ssl_monitor.py` [NEW]

- [ ] Daily check: all custom domain SSL certs >30 days from expiry
- [ ] Alert if any cert is within 7 days of expiry
- [ ] Force-renew via Traefik API if needed
- [ ] Dashboard indicator showing SSL status per domain

### 3. Log Retention & Cleanup
- [ ] Build logs: auto-delete after 30 days (configurable)
- [ ] Container logs: rotate and compress after 7 days
- [ ] Audit logs: keep for 365 days
- [ ] Celery task: `cleanup_old_logs_task` running weekly

### 4. Terms of Service & Legal Pages
File: `frontend/src/app/legal/` [NEW]

- [ ] Terms of Service page
- [ ] Privacy Policy page
- [ ] Acceptable Use Policy
- [ ] Cookie consent banner
- [ ] GDPR data export/deletion endpoint

### 5. Status Page
File: `frontend/src/app/status/page.tsx` [MODIFY]

- [ ] Real-time platform status (all services green/yellow/red)
- [ ] Incident history
- [ ] Uptime percentage per service
- [ ] Subscribe to status updates (email)

### 6. Documentation
File: `frontend/src/app/docs/` [MODIFY]

- [ ] API reference (auto-generated from OpenAPI schema)
- [ ] Getting started guide
- [ ] CLI documentation
- [ ] Dockerfile best practices for the platform
- [ ] Environment variables reference
- [ ] Custom domains setup guide
- [ ] Addon management guide

### 7. Email Templates
File: `backend/templates/email/` [NEW]

- [ ] Welcome email (on registration)
- [ ] Deploy success notification
- [ ] Deploy failed notification
- [ ] Invoice / receipt email
- [ ] Payment failed warning
- [ ] Password reset
- [ ] Team invite

### 8. Error Pages
File: `frontend/src/app/` [MODIFY]

- [ ] Custom 404 page (branded)
- [ ] Custom 500 page
- [ ] Maintenance mode page
- [ ] Rate limit exceeded page

### 9. SEO & Marketing
- [ ] Meta tags on all public pages
- [ ] OpenGraph images for social sharing
- [ ] Structured data (JSON-LD) for Google
- [ ] Sitemap.xml
- [ ] Blog/changelog page for feature announcements

### 10. Reseller / White-Label Support
File: `frontend/src/app/reseller/page.tsx` [EXISTS — verify completeness]

- [ ] Custom branding per reseller (logo, colors, domain)
- [ ] Reseller billing (they pay wholesale, charge retail)
- [ ] Reseller dashboard (their customers, their revenue)

## Validation
- Each item should be independently testable
- Security items should be penetration-tested
- Legal pages should be reviewed by legal counsel
- Documentation should be tested by a new user (first-time deploy)
