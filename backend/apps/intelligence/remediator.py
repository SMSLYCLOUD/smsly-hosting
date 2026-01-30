from typing import Dict, List, Optional
import logging

class RemediationEngine:
    """
    Suggests fixes based on analyzed failure patterns.
    """

    RECOMMENDATIONS = {
        'OOM_KILLED': {
            'action': 'SCALE_UP',
            'resource': 'MEMORY',
            'amount': '512MB',
            'message': 'Your service ran out of memory. We recommend increasing the memory limit by 512MB.'
        },
        'DB_CONNECTION_TIMEOUT': {
            'action': 'SCALE_UP_POOL',
            'resource': 'DB_POOL',
            'amount': 20,
            'message': 'Database connection pool exhausted. Increase connection limit or optimize queries.'
        },
        'CRASH_LOOP': {
            'action': 'ROLLBACK',
            'resource': 'DEPLOYMENT',
            'message': 'Service is crashing on startup. Reverting to the last stable deployment.'
        }
    }

    def suggest_fix(self, issue_type: str) -> Optional[Dict]:
        """
        Returns a suggested remediation action for a given issue type.
        """
        return self.RECOMMENDATIONS.get(issue_type)
