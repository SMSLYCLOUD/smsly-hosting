"""Frontend compatibility alias: /api/v1/ecosystem/cached-scan/

The frontend ``ecosystemApi.cachedScan`` (api.ts:2040) calls
``GET /api/v1/ecosystem/cached-scan/``. The canonical route
is mounted at ``/api/v1/cloud/ecosystem/cached-scan/`` and
the same ``IntelligenceViewSet.cached_scan_result`` action handles
both URLs. This alias keeps the existing frontend working
without a rebuild.
"""
from apps.cloud.views import IntelligenceViewSet
from django.urls import path

urlpatterns = [
    path('', IntelligenceViewSet.as_view({'get': 'cached_scan_result'}),
         name='ecosystem-cached-scan-alias'),
]
