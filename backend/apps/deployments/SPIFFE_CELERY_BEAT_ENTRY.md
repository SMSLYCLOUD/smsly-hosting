# SPIRE Celery Beat Schedule Entry
# =================================
# Add this to the beat_schedule dict in backend/config/celery.py
#
# This runs the SPIRE registration sync every 5 minutes to ensure
# all deployed services have SPIFFE identities registered.

# Add to beat_schedule:
#
# 'sync-spiffe-entries': {
#     'task': 'apps.deployments.tasks_spiffe.sync_spiffe_entries_task',
#     'schedule': crontab(minute='*/5'),
# },

# Add to task_routes:
#
# 'apps.deployments.tasks_spiffe.sync_spiffe_entries_task': {'queue': 'deploy'},
