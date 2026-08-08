"""
mTLS URL Configuration
======================
URL patterns for mTLS management API.
"""

from django.urls import path
from . import views

urlpatterns = [
    path(
        "services/<uuid:service_id>/mtls/status/",
        views.mtls_status,
        name="mtls-status",
    ),
    path(
        "services/<uuid:service_id>/mtls/enable/",
        views.mtls_enable,
        name="mtls-enable",
    ),
    path(
        "services/<uuid:service_id>/mtls/disable/",
        views.mtls_disable,
        name="mtls-disable",
    ),
    path(
        "mtls/health/",
        views.mtls_health,
        name="mtls-health",
    ),
    path(
        "mtls/configs/",
        views.mtls_list,
        name="mtls-list",
    ),
    # Authorization policies
    path(
        "mtls/policies/",
        views.policy_list_create,
        name="mtls-policy-list-create",
    ),
    path(
        "mtls/policies/<int:policy_id>/",
        views.policy_update_delete,
        name="mtls-policy-update-delete",
    ),
]
