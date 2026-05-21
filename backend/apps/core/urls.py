"""Core app URL patterns."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.views import ContactView, DashboardOverviewView, APIKeyViewSet, SubdomainStubViewSet, AdminUserViewSet, SystemResourcesView

router = DefaultRouter()
router.register(r'api-keys', APIKeyViewSet, basename='api-keys')
router.register(r'subdomains', SubdomainStubViewSet, basename='subdomains')
router.register(r'admin/users', AdminUserViewSet, basename='admin-users')

urlpatterns = [
    path('contact/', ContactView.as_view(), name='contact'),
    path('dashboard/overview/', DashboardOverviewView.as_view(), name='dashboard-overview'),
    path('system/resources/', SystemResourcesView.as_view(), name='system-resources'),
    path('', include(router.urls)),
]
