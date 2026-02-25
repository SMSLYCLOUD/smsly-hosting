from django.urls import path
from . import views

urlpatterns = [
    path('status/', views.autoscaler_status),
    path('history/', views.autoscaler_history),
    path('config/', views.autoscaler_config),
    path('trigger/', views.autoscaler_trigger),
]
