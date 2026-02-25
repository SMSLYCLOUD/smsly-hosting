## Task: Implement 7 platform features for SMSLY Hosting

Read `jules_feature_prompts.md` in the repo root. It contains exhaustive, step-by-step instructions for all 7 features listed below. Follow every instruction exactly as written.

### Features to implement (in this order):

1. **Real SVG Addon Logos** — Replace emoji icons in `frontend/src/components/addons/AddonsTab.tsx` with real SVG logos. Create files in `frontend/public/logos/addons/` for postgres, redis, mysql, mongodb, minio, qdrant, elasticsearch. Use `<Image>` from next/image.

2. **Addon Env Var Visibility + Shortcodes** — Add `parsed_credentials` property to `Addon` model. Add `source` field to `EnvironmentVariable` model. Auto-inject addon credentials as env vars after provisioning. Build shortcode resolver (`{{addon-name.DATABASE_URL}}`→ real value) in `backend/services/env_resolver.py`. Show credentials in AddonsTab UI with copy buttons. Mark ADDON-sourced vars as read-only in EnvVarsTab.

3. **GitHub Repo Caching** — New file `backend/services/repo_cache.py` with bare-clone + fetch strategy. Cache at `/opt/smsly-cache/repos/`. Add `filelock>=3.12` to requirements.txt. Integrate into deploy pipeline. Add cleanup management command.

4. **Resilient Platform Update System** — New model `PlatformUpdate` in `backend/apps/deployments/models_updates.py`. New engine `backend/services/platform_updater.py` with snapshot→pull→build→migrate→restart→health-check→auto-rollback flow. New API views in `backend/apps/deployments/views_updates.py`. Celery task. Frontend page at `frontend/src/app/settings/updates/page.tsx`. Register in urls.py.

5. **Zero-Downtime Service Migration** — New engine `backend/services/transfer_engine.py` that orchestrates the existing `ServerTransfer` model (already in `models_transfer.py`). Wire up `execute_server_transfer_task` and `rollback_transfer_task` in tasks.py. Add `paramiko>=3.4` to requirements.txt. Uses SSH to SCP backups to target server.

6. **Activity Feed UI** — New page `frontend/src/app/activity/page.tsx`. Fetches from existing `/api/v1/audit-logs/` endpoint. Timeline with colored dots, filters, auto-refresh. Add nav link.

7. **Resource Usage Alerts** — New model `ResourceAlert` in `backend/apps/notifications/models.py`. Frontend alert banner `frontend/src/components/dashboard/ResourceAlerts.tsx`. Register app, create migrations.

### Critical rules:
- Read `jules_feature_prompts.md` FIRST — it has the exact code for every file
- Follow existing codebase patterns: `DefaultRouter`, `ModelViewSet`, `IsAuthenticated`, ownership filtering via `get_queryset()`
- Frontend theme: bg-zinc-950, text-zinc-100, border-zinc-800, Lucide icons
- Never hardcode secrets — use `os.environ[]`
- Always filter querysets by `request.user`
- Use `EncryptedCharField` for sensitive data
- Create Django migrations for all new models

### Verification (ALL must pass):
```bash
cd backend && python manage.py makemigrations --check
cd backend && python manage.py check
cd frontend && npm run build
```
