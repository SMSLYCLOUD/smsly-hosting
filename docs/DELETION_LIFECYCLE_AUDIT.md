# Deletion Lifecycle Audit

## Current state
- The deletion logic lives synchronously in backend/apps/deployments/views.py inside perform_destroy methods (e.g. for ServiceViewSet and AddonViewSet).
- When a service is deleted, the backend directly tries to talk to Docker to kill containers by finding ID or by name patterns (slug). It then synchronously executes .delete() on the DB.
- Any error during Docker cleanup logs an error and ignores it, proceeding to delete the DB record.
- This creates zombie containers if there is any issue with docker (e.g., timeout, container locked, API down temporarily).
- There's no background orchestration task, no retry mechanism, no pending state. It just assumes success.

## Broken Points
- Sync Docker calls without timeout safety.
- Swallowed errors (bare except Exception: pass).
- DB row deleted before verifying the underlying infrastructure deletion.
- No single source of truth (labels) to find all related resources (volumes, networks, Traefik routes).
- Missing logic for related resources like volumes, networks, celery tasks.
- No 'retry deletion' mechanism.

## Required Fixes
- Create a DeletionOrchestrator service that handles reliable asynchronous cleanup of resources.
- Make API endpoints return a 202 Pending status and queue a Celery task to delete.
- In the DB model, add a status field with deletion_pending and deletion_failed. Wait for actual docker completion before deleting DB record, or just set it to deleted (soft-delete style or hard delete only when clean).
- Create management commands to list and clean up orphaned docker resources.
