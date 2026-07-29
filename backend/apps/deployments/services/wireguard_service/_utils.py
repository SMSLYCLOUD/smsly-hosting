import logging

logger = logging.getLogger(__name__)


def _command_text(result) -> str:
    if isinstance(result, tuple):
        stdout = result[0] if len(result) > 0 else ""
        stderr = result[1] if len(result) > 1 else ""
        return (stdout or "") + (("\n" + stderr) if stderr else "")
    return "" if result is None else str(result)


def _bounded_error(exc, limit=2000) -> str:
    return str(exc).replace("\x00", "")[:limit]
