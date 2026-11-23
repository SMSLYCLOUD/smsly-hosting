from __future__ import annotations

from typing import Any

ADDON_ALERT_RULES = [
    {'metric': 'connection_count', 'threshold': 90, 'severity': 'warning',
     'message': 'Approaching max connections'},
    {'metric': 'memory_usage_percent', 'threshold': 90,
     'severity': 'warning', 'message': 'Memory pressure detected'},
]

def check_alerts(addon: Any, metrics: dict[str, Any]) -> list[dict[str, str]]:
    alerts = []
    status = str((metrics or {}).get('status') or 'ONLINE').upper()
    if status in ('OFFLINE', 'FAILED', 'UNHEALTHY', 'ERROR'):
        alerts.append({
            'addon': getattr(addon, 'name', str(getattr(addon, 'id', 'addon'))),
            'severity': 'critical',
            'message': f"Addon is {status}" + (f": {metrics.get('error')}" if metrics.get('error') else ""),
        })
    for rule in ADDON_ALERT_RULES:
        val = (metrics or {}).get(rule['metric'])
        if val is not None and val >= rule['threshold']:
            alerts.append({
                'addon': addon.name,
                'severity': rule['severity'],
                'message': rule['message']
            })
    return alerts
