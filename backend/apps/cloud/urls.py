"""Urls module."""
from rest_framework.routers import DefaultRouter
from .views import CloudProviderViewSet, CloudResourceViewSet, IntelligenceViewSet, EcosystemViewSet

router = DefaultRouter()
router.register(r'providers', CloudProviderViewSet, basename='providers')
router.register(r'resources', CloudResourceViewSet, basename='resources')
router.register(r'intelligence', IntelligenceViewSet, basename='intelligence')
router.register(r'ecosystem', EcosystemViewSet, basename='ecosystem')

urlpatterns = router.urls

