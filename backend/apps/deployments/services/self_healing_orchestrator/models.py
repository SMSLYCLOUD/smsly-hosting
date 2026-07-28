from dataclasses import dataclass, field

from .enums import FailureType, RecoveryAction


@dataclass
class DiagnosticResult:
    """Structured result from a diagnostic run."""
    success: bool = True
    failure_type: FailureType = FailureType.UNKNOWN
    container_logs: str = ""
    container_status: str = ""
    container_state: str = ""
    disk_usage_pct: float = 0.0
    memory_usage_pct: float = 0.0
    docker_running: bool = False
    network_reachable: bool = False
    error_details: str = ""
    suggested_actions: list = field(default_factory=list)
    raw_diagnostics: dict = field(default_factory=dict)


@dataclass
class RecoveryResult:
    """Structured result from a recovery attempt."""
    success: bool = False
    action_taken: RecoveryAction = RecoveryAction.NONE
    details: str = ""
    post_recovery_status: str = ""
    next_action: RecoveryAction | None = None
