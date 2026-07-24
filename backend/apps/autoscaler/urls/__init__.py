from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .. import views
from ..views.service import ScalingViewSet

router = DefaultRouter()
router.register(r'services', ScalingViewSet, basename='autoscaler-services')

urlpatterns = [
    path('status/', views.autoscaler_status),
    path('history/', views.autoscaler_history),
    path('config/', views.autoscaler_config),
    path('trigger/', views.autoscaler_trigger),
    path('scale/', views.autoscaler_scale, name='autoscaler_scale'),
    path('', include(router.urls)),
]
