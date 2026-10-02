"""URLs for autoscaler app.

Naming convention: URL names use kebab-case (e.g. 'autoscaler-scale').
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .. import views
from ..views.service import ScalingViewSet
from ..views.nodes import AutoscalerNodesViewSet

router = DefaultRouter()
router.register(r'services', ScalingViewSet, basename='autoscaler-services')
router.register(r'nodes', AutoscalerNodesViewSet, basename='autoscaler-nodes')

urlpatterns = [
    path('status/', views.autoscaler_status),
    path('history/', views.autoscaler_history),
    path('config/', views.autoscaler_config),
    path('trigger/', views.autoscaler_trigger),
    path('scale/', views.autoscaler_scale, name='autoscaler-scale'),
    path('', include(router.urls)),
]
