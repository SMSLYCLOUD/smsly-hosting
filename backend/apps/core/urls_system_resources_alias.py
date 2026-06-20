"""Frontend compatibility alias: /api/v1/system/resources/

The frontend ``systemApi.resources`` (lib/api.ts:845) calls
``GET /api/v1/system/resources/`` to render the sidebar CPU/RAM
widget. The canonical route is at
``/api/v1/core/system/resources/``. This alias keeps the
existing frontend working without requiring a rebuild.
"""
from apps.core.views import SystemResourcesView
from django.urls import path

urlpatterns = [
    path('', SystemResourcesView.as_view(), name='system-resources-alias'),
]
