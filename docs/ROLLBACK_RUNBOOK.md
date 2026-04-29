# Rollback Runbook
1. User confirms rollback.
2. API validates target deployment status and artifact presence.
3. New queued rollback deployment is created.
4. Deploy task is dispatched on active provider.
5. API returns rollback metadata and structured errors when blocked.
