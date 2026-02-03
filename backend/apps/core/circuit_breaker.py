"""
Circuit Breaker Middleware for SMSLY Hosting.
Prevents cascade failures by failing fast when dependencies are unhealthy.
"""
import time
import threading
import functools
import logging
from typing import Callable, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """
    Circuit Breaker implementation for protecting against cascade failures.

    Usage:
        breaker = CircuitBreaker(name="database", failure_threshold=5)

        @breaker
        def database_call():
            # risky operation
            pass
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        expected_exception: type = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(f"Circuit {self.name}: OPEN -> HALF_OPEN")
            return self._state

    def _handle_success(self):
        """Reset circuit on success."""
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED")

    def _handle_failure(self, exception: Exception):
        """Track failure and potentially open circuit."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Circuit {
                        self.name}: OPENED after {
                        self._failure_count} failures. "
                    f"Last error: {exception}"
                )

    def __call__(self, func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpen(
                    f"Circuit {
                        self.name} is OPEN. Failing fast to prevent cascade failure."
                )

            try:
                result = func(*args, **kwargs)
                self._handle_success()
                return result
            except self.expected_exception as e:
                self._handle_failure(e)
                raise

        return wrapper

    def reset(self):
        """Manually reset the circuit breaker."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            logger.info(f"Circuit {self.name}: Manually reset to CLOSED")


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open and request is rejected."""
    pass


# Pre-configured circuit breakers for common dependencies
database_breaker = CircuitBreaker(
    name="database",
    failure_threshold=5,
    recovery_timeout=30
)

redis_breaker = CircuitBreaker(
    name="redis",
    failure_threshold=10,
    recovery_timeout=15
)

docker_breaker = CircuitBreaker(
    name="docker",
    failure_threshold=3,
    recovery_timeout=60
)

registry_breaker = CircuitBreaker(
    name="registry",
    failure_threshold=3,
    recovery_timeout=45
)
