"""Organization URL routing."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OrganizationViewSet, OrganizationSSOViewSet

router = DefaultRouter()
router.register(r'', OrganizationViewSet, basename='organization')
router.register(r'sso', OrganizationSSOViewSet, basename='org-sso')

urlpatterns = [
    path('', include(router.urls)),
]
