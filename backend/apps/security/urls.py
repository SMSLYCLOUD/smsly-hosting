"""URL routing for SMSLY Aegis Unified Security Command Center (/api/v1/security/)."""
from django.urls import path

from .views import (
    SecurityAiHuntView,
    SecurityAttackPathsView,
    SecurityComplianceView,
    SecurityFindingDetailView,
    SecurityFindingsListView,
    SecurityForensicsListView,
    SecurityMitreView,
    SecurityOcsfEventsView,
    SecurityOverviewView,
    SecurityPolicyView,
    SecuritySbomView,
    SecuritySoarExecuteView,
)

urlpatterns = [
    path("overview/", SecurityOverviewView.as_view(), name="security-overview"),
    path("findings/", SecurityFindingsListView.as_view(), name="security-findings"),
    path("findings/<uuid:finding_id>/", SecurityFindingDetailView.as_view(), name="security-finding-detail"),
    path("events/", SecurityOcsfEventsView.as_view(), name="security-ocsf-events"),
    path("attack-paths/", SecurityAttackPathsView.as_view(), name="security-attack-paths"),
    path("mitre/", SecurityMitreView.as_view(), name="security-mitre"),
    path("sbom/", SecuritySbomView.as_view(), name="security-sbom"),
    path("compliance/", SecurityComplianceView.as_view(), name="security-compliance"),
    path("policy/", SecurityPolicyView.as_view(), name="security-policy"),
    path("soar/execute/", SecuritySoarExecuteView.as_view(), name="security-soar-execute"),
    path("forensics/", SecurityForensicsListView.as_view(), name="security-forensics"),
    path("ai-hunt/", SecurityAiHuntView.as_view(), name="security-ai-hunt"),
]
