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
        "services/<uuid:service_id>/mtls/sidecar/",
        views.mtls_sidecar_toggle,
        name="mtls-sidecar-toggle",
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
    path(
        "mtls/spire/deploy/",
        views.mtls_spire_deploy,
        name="mtls-spire-deploy",
    ),
    path(
        "mtls/spire/undeploy/",
        views.mtls_spire_undeploy,
        name="mtls-spire-undeploy",
    ),
]
