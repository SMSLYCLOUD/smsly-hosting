from django.urls import path
from . import views

urlpatterns = [
    path("decisions/", views.crowdsec_decisions, name="crowdsec-decisions"),
    path("decisions/service/<uuid:service_id>/", views.crowdsec_service_decisions, name="crowdsec-service-decisions"),
    path("alerts/", views.crowdsec_alerts, name="crowdsec-alerts"),
    path("alerts/service/<uuid:service_id>/", views.crowdsec_service_alerts, name="crowdsec-service-alerts"),
    path("unban/", views.crowdsec_unban, name="crowdsec-unban"),
    path("metrics/", views.crowdsec_metrics, name="crowdsec-metrics"),
]