class PipelineError(Exception):
    """Base class for pipeline failures."""


class BuildError(PipelineError):
    """Raised when the build step fails (user error typically)."""


class InfraError(PipelineError):
    """Raised when system infrastructure fails."""
