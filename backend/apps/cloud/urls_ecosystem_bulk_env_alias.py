"""Frontend compatibility alias: /api/v1/ecosystem/bulk-update-environment/

The frontend ``ecosystemApi.bulkUpdateEnvironment`` (api.ts:2039)
calls ``POST /api/v1/ecosystem/bulk-update-environment/`` to set
env vars on multiple services at once. The canonical route is
mounted at ``/api/v1/cloud/ecosystem/bulk-env/`` and the same
``IntelligenceViewSet.ecosystem_bulk_env`` action handles both
URLs. This alias keeps the existing frontend working without a
rebuild.
"""
from apps.cloud.views import IntelligenceViewSet
from django.urls import path

urlpatterns = [
    path('', IntelligenceViewSet.as_view({'post': 'ecosystem_bulk_env'}),
         name='ecosystem-bulk-update-environment-alias'),
]
