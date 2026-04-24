import requests
import time
from typing import Tuple
from apps.deployments.models_safedeploy import HealthCheckResult

def perform_health_check(url: str) -> Tuple[bool, HealthCheckResult]:
    start_time = time.time()
    try:
        response = requests.get(url, timeout=10)
        elapsed_ms = int((time.time() - start_time) * 1000)

        status = HealthCheckResult.Status.SUCCESS if response.status_code < 400 else HealthCheckResult.Status.FAILED

        result = HealthCheckResult(
            url=url,
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            status=status
        )
        return status == HealthCheckResult.Status.SUCCESS, result
    except requests.RequestException as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        result = HealthCheckResult(
            url=url,
            response_time_ms=elapsed_ms,
            status=HealthCheckResult.Status.FAILED,
            error_message=str(e)
        )
        return False, result
