"""Urls module for the global /api/v1/domains/ endpoint."""
from rest_framework.routers import DefaultRouter

from ..views import GlobalDomainViewSet

router = DefaultRouter()
router.register(r'', GlobalDomainViewSet, basename='global-domain')

urlpatterns = router.urls
