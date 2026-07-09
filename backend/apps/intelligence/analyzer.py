"""Analyzer module."""
import logging
import re

from .providers import _cached_ask

logger = logging.getLogger(__name__)


class LogAnalyzer:
    """
    Analyzes application logs to detect common failure patterns.
    """

    PATTERNS = {
        'OOM_KILLED': [
            r"OOM command not allowed",
            r"Out of memory: Kill process",
            r"java.lang.OutOfMemoryError",
            r"JavaScript heap out of memory"
        ],
        'DB_CONNECTION_TIMEOUT': [
            r"psycopg2.OperationalError: FATAL: remaining connection slots are reserved",
            r"SequelizeConnectionError",
            r"MongoTimeoutError"
        ],
        'CRASH_LOOP': [
            r"Back-off restarting failed container",
            r"CrashLoopBackOff",
            r"restart count exceeded",
        ],
        'SSL_CERT_EXPIRED': [
            r"SSL_ERROR_EXPIRED_CERT_ALERT",
            r"certificate has expired",
            r"SSL certificate problem: certificate has expired",
        ],
        'DISK_FULL': [
            r"No space left on device",
            r"ENOSPC",
            r"disk quota exceeded",
        ],
        'PORT_CONFLICT': [
            r"EADDRINUSE",
            r"address already in use",
            r"port is already allocated",
        ],
        'DNS_FAILURE': [
            r"EAI_NONAME",
            r"Name or service not known",
            r"NXDOMAIN",
            r"getaddrinfo failed",
        ],
        'DEPENDENCY_MISSING': [
            r"ModuleNotFoundError",
            r"ImportError",
            r"Cannot find module",
            r"Module not found",
        ],
        'BUILD_FAILURE': [
            r"ERROR: failed to solve",
            r"npm ERR!",
            r"pip install.*failed",
            r"cargo build.*error",
            r"SyntaxError",
        ],
        'PERMISSION_DENIED': [
            r"Permission denied",
            r"EACCES",
            r"Operation not permitted",
        ],
        'TIMEOUT': [
            r"TimeoutError",
            r"context deadline exceeded",
            r"request timeout",
            r"ETIMEDOUT",
        ],
        'RATE_LIMITED': [
            r"429 Too Many Requests",
            r"rate limit exceeded",
            r"RateLimitError",
        ],
        'HEALTH_CHECK_FAIL': [
            r"health check failed",
            r"unhealthy",
            r"readiness probe failed",
        ],
    }

    def analyze_logs(self, logs: str) -> list[dict[str, str]]:
        """
        Scans logs against known failure patterns.
        Returns a list of detected issues.
        """
        detected_issues = []

        for issue_type, regex_list in self.PATTERNS.items():
            for pattern in regex_list:
                if re.search(pattern, logs, re.IGNORECASE):
                    detected_issues.append({
                        'type': issue_type,
                        'pattern': pattern,
                        'confidence': '0.95'  # Regex match is high confidence
                    })
                    break  # Found one match for this type, move to next type

        return detected_issues

    def generate_diagnosis(self, logs: str) -> str:
        """
        Generates a human-readable diagnosis using detected patterns or LLM simulation.
        """
        issues = self.analyze_logs(logs)
        if issues:
            return self._format_known_issues(issues)

        # For unknown issues, use real AI analysis
        if len(logs) > 200:  # Only call AI if there's substantial log content
            try:
                prompt = (
                    f"Analyze these deployment logs and diagnose the issue. "
                    f"Be concise (max 3 sentences):\n\n{logs[-5000:]}"
                )
                response, provider = _cached_ask(prompt, mode="code_review")
                return f"[{provider}] {response}"
            except Exception as e:
                logger.warning("AI diagnosis failed: %s", e)

        return "No obvious issues detected."

    def _format_known_issues(self, issues: list[dict[str, str]]) -> str:
        descriptions = []
        for issue in issues:
            t = issue['type']
            if t == 'OOM_KILLED':
                descriptions.append("The application ran out of memory. Consider upgrading the plan.")
            elif t == 'DB_CONNECTION_TIMEOUT':
                descriptions.append("Database connection failed. Check your credentials or pool size.")
            elif t == 'CRASH_LOOP':
                descriptions.append("The application is crashing repeatedly on startup.")
            else:
                descriptions.append(f"Detected {t.replace('_', ' ').lower()} issue.")
        return " ".join(descriptions)
