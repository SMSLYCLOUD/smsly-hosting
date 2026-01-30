import re
import logging
from typing import List, Dict, Optional

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
            r"Error: EADDRINUSE"
        ]
    }

    def analyze_logs(self, logs: str) -> List[Dict[str, str]]:
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
                        'confidence': 0.95  # Regex match is high confidence
                    })
                    break  # Found one match for this type, move to next type

        return detected_issues
