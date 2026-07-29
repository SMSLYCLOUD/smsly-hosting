import logging
import time
from urllib.parse import urlparse

import requests
import urllib3
from requests.exceptions import RequestException

from apps.deployments.models.safedeploy import HealthCheckResult

logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def perform_health_check(
    url: str,
    service=None,
    max_retries: int = 6,
    retry_delay: float = 3.0,
    verify_ssl: bool = False,
) -> tuple[bool, HealthCheckResult]:
    """
    Performs an HTTP health check against the target URL with retry backoff
    to accommodate container cold boots, DNS propagation, and TLS initialization.
    """
    target_url = url
    if service and getattr(service, "health_check_path", None):
        path = service.health_check_path.strip()
        if path and path != "/":
            if not path.startswith("/"):
                path = f"/{path}"
            parsed = urlparse(target_url)
            if not parsed.path or parsed.path == "/":
                target_url = f"{target_url.rstrip('/')}{path}"

    start_time = time.time()
    last_error = None
    last_status_code = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                target_url,
                timeout=10,
                verify=verify_ssl,
                allow_redirects=True,
            )
            last_status_code = response.status_code
            if response.status_code < 400:
                elapsed_ms = int((time.time() - start_time) * 1000)
                result = HealthCheckResult(
                    service=service,
                    url=target_url,
                    status_code=response.status_code,
                    response_time_ms=elapsed_ms,
                    status=HealthCheckResult.Status.SUCCESS,
                )
                result.save()
                return True, result
            last_error = f"HTTP {response.status_code}"
        except RequestException as exc:
            last_error = str(exc)

        if attempt < max_retries:
            logger.info(
                "Health check attempt %d/%d for %s failed (%s), retrying in %.1fs...",
                attempt, max_retries, target_url, last_error, retry_delay
            )
            time.sleep(retry_delay)

    elapsed_ms = int((time.time() - start_time) * 1000)
    result = HealthCheckResult(
        service=service,
        url=target_url,
        status_code=last_status_code,
        response_time_ms=elapsed_ms,
        status=HealthCheckResult.Status.FAILED,
        error_message=last_error or "Health check failed after retries",
    )
    result.save()
    return False, result
