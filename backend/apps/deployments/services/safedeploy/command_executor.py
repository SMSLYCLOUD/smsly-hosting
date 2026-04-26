import subprocess
import os
import logging
from typing import Tuple, Dict
from .redaction import redact_secrets

logger = logging.getLogger(__name__)

class CommandExecutor:
    def run(self, cmd: str, cwd: str, env: Dict[str, str] = None, timeout: int = 120) -> Tuple[int, str, str]:
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        logger.info(f"Executing command in {cwd}: {cmd}")
        try:
            result = subprocess.run(cmd, shell=True, cwd=cwd, env=run_env, capture_output=True, text=True, timeout=timeout)
            stdout = redact_secrets(result.stdout)
            stderr = redact_secrets(result.stderr)
            return result.returncode, stdout, stderr
        except subprocess.TimeoutExpired as e:
            logger.warning(f"Command timed out: {cmd}")
            stdout = redact_secrets(e.stdout.decode() if e.stdout else "")
            stderr = redact_secrets(e.stderr.decode() if e.stderr else "Command timed out")
            return 124, stdout, stderr
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return 1, "", str(e)
