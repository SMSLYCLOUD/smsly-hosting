import time

import requests

from apps.deployments.models_safedeploy import HealthCheckResult


def perform_health_check(url: str, service=None) -> tuple[bool, HealthCheckResult]:
    start_time = time.time()
    try:
        response = requests.get(url, timeout=10)
        elapsed_ms = int((time.time() - start_time) * 1000)

        status = HealthCheckResult.Status.SUCCESS if response.status_code < 400 else HealthCheckResult.Status.FAILED

        result = HealthCheckResult(
            service=service,
            url=url,
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            status=status,
        )
        result.save()
        return status == HealthCheckResult.Status.SUCCESS, result
    except requests.RequestException as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        result = HealthCheckResult(
            service=service,
            url=url,
            response_time_ms=elapsed_ms,
            status=HealthCheckResult.Status.FAILED,
            error_message=str(e),
        )
        result.save()
        return False, result
