from .base import BaseOrchestratorMixin
from .diagnostics import DiagnosticsMixin
from .recovery import RecoveryMixin
from .ai_escalation import AIEscalationMixin
from .status import StatusMixin


class SelfHealingOrchestrator(
    BaseOrchestratorMixin,
    DiagnosticsMixin,
    RecoveryMixin,
    AIEscalationMixin,
    StatusMixin,
):
    """
    Orchestrates automated diagnosis and recovery for remote node failures.

    Usage::

        orchestrator = SelfHealingOrchestrator(server)
        result = orchestrator.heal_deployment_failure(deployment)
    """
