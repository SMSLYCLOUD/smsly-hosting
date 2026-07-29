"""
Graceful Shutdown Handler for SMSLY Hosting.
Ensures clean shutdown of background tasks and connections.
"""
import logging
import signal
import sys
import threading

from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """
    Manages graceful shutdown of the application.
    Ensures in-flight requests complete and connections are closed cleanly.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.shutdown_requested = False
        self.active_requests = 0
        self._lock = threading.Lock()
        self._initialized = True

    def register_signals(self):
        """Register signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        logger.info("Graceful shutdown handlers registered")

    def _handle_signal(self, signum, _frame):
        """Handle shutdown signal."""
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name}, initiating graceful shutdown...")

        self.shutdown_requested = True

        # Wait for active requests to complete (max 30 seconds)
        import time
        timeout = 30
        start = time.time()

        while self.active_requests > 0 and (time.time() - start) < timeout:
            logger.info(
                f"Waiting for {self.active_requests} active requests...")
            time.sleep(1)

        if self.active_requests > 0:
            logger.warning(
                f"Forcefully shutting down with {self.active_requests} active requests")

        self._cleanup()
        sys.exit(0)

    def _cleanup(self):
        """Cleanup resources before shutdown."""
        try:
            # Close database connections
            connection.close()
            logger.info("Database connections closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")

        try:
            # Clear ephemeral cache entries
            cache.clear()
            logger.info("Cache cleared")
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")

        logger.info("Graceful shutdown complete")

    def request_started(self):
        """Track when a request starts."""
        with self._lock:
            self.active_requests += 1

    def request_finished(self):
        """Track when a request finishes."""
        with self._lock:
            self.active_requests = max(0, self.active_requests - 1)

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been requested."""
        return self.shutdown_requested


class GracefulShutdownMiddleware:
    """
    Django middleware to track active requests and reject new ones during shutdown.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.shutdown_handler = GracefulShutdown()

    def __call__(self, request):
        # Reject new requests if shutting down (except health checks)
        if self.shutdown_handler.is_shutting_down:
            if not request.path.startswith('/health'):
                from django.http import JsonResponse
                # Use the same envelope as smsly_exception_handler so the
                # frontend has one shape for all error responses. Retry-After
                # header tells clients (and load balancers) when to retry.
                response = JsonResponse(
                    {
                        "error": "Service is shutting down",
                        "code": "service_shutting_down",
                        "status": 503,
                    },
                    status=503,
                )
                response["Retry-After"] = "30"
                return response

        self.shutdown_handler.request_started()
        try:
            response = self.get_response(request)
            return response
        finally:
            self.shutdown_handler.request_finished()


# Initialize on module load
shutdown_handler = GracefulShutdown()
shutdown_handler.register_signals()
