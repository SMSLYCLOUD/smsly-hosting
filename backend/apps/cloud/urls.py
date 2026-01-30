from rest_framework.routers import DefaultRouter
from .views import CloudProviderViewSet, CloudResourceViewSet, IntelligenceViewSet

router = DefaultRouter()
router.register(r'providers', CloudProviderViewSet, basename='providers')
router.register(r'resources', CloudResourceViewSet, basename='resources')
router.register(r'intelligence', IntelligenceViewSet, basename='intelligence')

urlpatterns = router.urls
