"""Core app URL patterns."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.views import ContactView, DashboardOverviewView, APIKeyViewSet

router = DefaultRouter()
router.register(r'api-keys', APIKeyViewSet, basename='api-keys')

urlpatterns = [
    path('contact/', ContactView.as_view(), name='contact'),
    path('dashboard/overview/', DashboardOverviewView.as_view(), name='dashboard-overview'),
    path('', include(router.urls)),
]
