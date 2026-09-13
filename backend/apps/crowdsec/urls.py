from django.urls import path
from . import views

urlpatterns = [
    path("system/crowdsec/decisions/", views.crowdsec_decisions, name="crowdsec-decisions"),
    path("system/crowdsec/decisions/service/<uuid:service_id>/", views.crowdsec_service_decisions, name="crowdsec-service-decisions"),
    path("system/crowdsec/alerts/", views.crowdsec_alerts, name="crowdsec-alerts"),
    path("system/crowdsec/alerts/service/<uuid:service_id>/", views.crowdsec_service_alerts, name="crowdsec-service-alerts"),
    path("system/crowdsec/unban/", views.crowdsec_unban, name="crowdsec-unban"),
    path("system/crowdsec/metrics/", views.crowdsec_metrics, name="crowdsec-metrics"),
]