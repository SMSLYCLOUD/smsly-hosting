"""Frontend compatibility alias: /api/v1/dashboard/overview/

The frontend calls ``GET /api/v1/dashboard/overview/`` but the
canonical route is mounted at ``/api/v1/core/dashboard/overview/``.
This alias keeps the existing frontend working without requiring
a rebuild. New code should call the canonical path.
"""
from apps.core.views import DashboardOverviewView
from django.urls import path

urlpatterns = [
    path('', DashboardOverviewView.as_view(), name='dashboard-overview-alias'),
]
