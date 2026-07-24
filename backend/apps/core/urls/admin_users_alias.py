"""Frontend compatibility alias: /api/v1/admin/users/

The frontend ``coreApi.adminGetUsers / adminUpdateUser``
(lib/api.ts:1649, 1653) call GET/PATCH on
``/api/v1/admin/users/``. The canonical route is mounted at
``/api/v1/core/admin/users/`` (r'admin/users' router under
core app). This alias keeps the existing frontend working
without a rebuild.
"""
from apps.core.views import AdminUserViewSet
from django.urls import include, path
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'', AdminUserViewSet, basename='admin-users-alias')

urlpatterns = [
    path('', include(router.urls)),
]
