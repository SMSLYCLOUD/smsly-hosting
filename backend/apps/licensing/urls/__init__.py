from django.urls import include, path
from rest_framework.routers import DefaultRouter

from ..views import LicenseViewSet

router = DefaultRouter()
router.register(r'', LicenseViewSet, basename='licensing')

urlpatterns = [
    path('', include(router.urls)),
]
