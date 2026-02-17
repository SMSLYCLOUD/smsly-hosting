"""URLs for cloud app."""
from rest_framework.routers import DefaultRouter
from apps.cloud.views import CloudProviderViewSet, CloudResourceViewSet, IntelligenceViewSet

router = DefaultRouter()
router.register(r'providers', CloudProviderViewSet, basename='providers')
router.register(r'resources', CloudResourceViewSet, basename='resources')
router.register(r'intelligence', IntelligenceViewSet, basename='intelligence')
# Ecosystem actions are now part of IntelligenceViewSet
# router.register(r'ecosystem', EcosystemViewSet, basename='ecosystem')

urlpatterns = router.urls
