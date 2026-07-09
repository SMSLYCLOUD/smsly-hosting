"""
Runtime Error Resolver — pattern-based auto-diagnosis for container logs.

Scans runtime logs for known error patterns and returns actionable fixes.
Each pattern has:
  - regex to match the error
  - human-readable diagnosis
  - auto-fix action (env var to inject, config to change, etc.)
  - severity (critical / warning / info)

Called by the pipeline health-check stage and the AI diagnosis task.
"""

import logging
import re
from typing import Any, cast

from apps.deployments.models import EnvironmentVariable  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


# ─── Known Error Patterns ────────────────────────────────────────────
# Each pattern: (compiled_regex, category, diagnosis, auto_fix_dict | None)
# auto_fix_dict keys:
#   env: dict of {KEY: value} to auto-inject
#   port: str — correct PORT value to set
#   action: str — description for the user

ERROR_PATTERNS: list[dict[str, Any]] = [
    # ── Django ────────────────────────────────────────────────────────
    {
        'regex': re.compile(
            r'django\.core\.exceptions\.ImproperlyConfigured.*DJANGO_SETTINGS_MODULE',
            re.IGNORECASE | re.DOTALL
        ),
        'category': 'Django Configuration',
        'severity': 'critical',
        'diagnosis': (
            'Django cannot start because DJANGO_SETTINGS_MODULE is not set. '
            'The app does not know where to find its settings.'
        ),
        'auto_fix': {
            'env_detect': 'DJANGO_SETTINGS_MODULE',
            'env_pattern': re.compile(r'File ".*?/(\w+)/(?:asgi|wsgi|settings)\.py"'),
            'fallback': '{project}.settings',
            'action': 'Auto-detected Django project and set DJANGO_SETTINGS_MODULE',
        },
    },
    {
        'regex': re.compile(
            r'django\.core\.exceptions\.ImproperlyConfigured.*ALLOWED_HOSTS',
            re.IGNORECASE | re.DOTALL
        ),
        'category': 'Django Configuration',
        'severity': 'critical',
        'diagnosis': (
            'Django is rejecting requests because ALLOWED_HOSTS is not configured. '
            'The domain needs to be added to ALLOWED_HOSTS.'
        ),
            'auto_fix': {
                'env': {'ALLOWED_HOSTS': 'localhost,127.0.0.1'},
                'action': 'Set ALLOWED_HOSTS to localhost only — add your domain in production',
            },
    },
    {
        'regex': re.compile(
            r'django\.db\.utils\.OperationalError.*could not connect to server',
            re.IGNORECASE | re.DOTALL
        ),
        'category': 'Database Connection',
        'severity': 'critical',
        'diagnosis': (
            'Django cannot connect to the database. Check that DATABASE_URL '
            'or individual DB_ env vars are set, and the database addon is provisioned.'
        ),
        'auto_fix': None,  # Can't auto-fix — needs addon or external DB
    },
    {
        'regex': re.compile(
            r'django\.db\.utils\.ProgrammingError.*relation.*does not exist',
            re.IGNORECASE | re.DOTALL
        ),
        'category': 'Database Migration',
        'severity': 'critical',
        'diagnosis': (
            'Database tables are missing. The app connected to the database '
            'but migrations have not been applied. Add a release command: '
            'python manage.py migrate'
        ),
        'auto_fix': {
            'env': {'RELEASE_COMMAND': 'python manage.py migrate'},
            'action': 'Set RELEASE_COMMAND to auto-run migrations before start',
        },
    },
    {
        'regex': re.compile(
            r'SECRET_KEY.*must not be empty|SECRET_KEY.*not set|KeyError.*SECRET_KEY',
            re.IGNORECASE
        ),
        'category': 'Django Security',
        'severity': 'critical',
        'diagnosis': (
            'Django SECRET_KEY is not set. This is required for cryptographic '
            'operations. A random key will be auto-generated.'
        ),
        'auto_fix': {
            'env_generate': {'SECRET_KEY': 50},  # 50-char random string
            'action': 'Auto-generated a random SECRET_KEY',
        },
    },

    # ── Node.js / JavaScript ──────────────────────────────────────────
    {
        'regex': re.compile(
            r'Error: Cannot find module [\'"](.+?)[\'"]',
            re.IGNORECASE
        ),
        'category': 'Node.js Missing Module',
        'severity': 'critical',
        'diagnosis': (
            'Node.js cannot find a required module. This usually means '
            'npm install was not run or a dependency is missing from package.json.'
        ),
        'auto_fix': None,
    },
    {
        'regex': re.compile(
            r'EADDRINUSE.*:(\d+)',
            re.IGNORECASE
        ),
        'category': 'Port Conflict',
        'severity': 'critical',
        'diagnosis': (
            'The app is trying to bind to a port that is already in use. '
            'Set the PORT environment variable to ensure the app uses the correct port.'
        ),
        'auto_fix': {
            'env': {'PORT': '8000'},
            'action': 'Set PORT=8000 to avoid port conflicts',
        },
    },
    {
        'regex': re.compile(
            r'ECONNREFUSED.*(?:5432|3306|6379|27017)',
            re.IGNORECASE
        ),
        'category': 'Database Connection',
        'severity': 'critical',
        'diagnosis': (
            'App cannot connect to a database or cache service. Ensure the '
            'DATABASE_URL / REDIS_URL env vars are set and the addon is provisioned.'
        ),
        'auto_fix': None,
    },

    # ── Python General ────────────────────────────────────────────────
    {
        'regex': re.compile(
            r'ModuleNotFoundError: No module named [\'"](.+?)[\'"]',
            re.IGNORECASE
        ),
        'category': 'Python Missing Module',
        'severity': 'critical',
        'diagnosis': (
            'A Python module is missing. This usually means a dependency is '
            'not in requirements.txt or the virtual environment is incomplete.'
        ),
        'auto_fix': None,
    },
    {
        'regex': re.compile(
            r'PermissionError.*Permission denied',
            re.IGNORECASE
        ),
        'category': 'File Permissions',
        'severity': 'warning',
        'diagnosis': (
            'The app does not have permission to access a file or directory. '
            'Check volume mount permissions or run chmod in the Dockerfile.'
        ),
        'auto_fix': None,
    },

    # ── Health / Port ─────────────────────────────────────────────────
    {
        'regex': re.compile(
            r'bind.*0\.0\.0\.0:(\d+)',
            re.IGNORECASE
        ),
        'category': 'Port Binding',
        'severity': 'info',
        'diagnosis': 'Detected the port the app is binding to.',
        'auto_fix': {
            'port_detect': True,
            'action': 'Auto-detected listening port from logs',
        },
    },
    {
        'regex': re.compile(
            r'Listening on.*?:(\d+)|Server started on.*?:(\d+)|'
            r'Running on.*?:(\d+)|Started on port (\d+)',
            re.IGNORECASE
        ),
        'category': 'Port Detection',
        'severity': 'info',
        'diagnosis': 'Detected the port the app is listening on.',
        'auto_fix': {
            'port_detect': True,
            'action': 'Auto-detected listening port from logs',
        },
    },

    # ── SSL / TLS ─────────────────────────────────────────────────────
    {
        'regex': re.compile(
            r'ERR_SSL_PROTOCOL_ERROR|SSL_ERROR_RX_RECORD_TOO_LONG|'
            r'ssl\.SSLError|CERT_NOT_FOUND',
            re.IGNORECASE
        ),
        'category': 'SSL Configuration',
        'severity': 'critical',
        'diagnosis': (
            'SSL/TLS is misconfigured. The platform handles SSL automatically '
            'via Traefik + Let\'s Encrypt. Ensure the domain DNS points to the '
            'server IP and the container has the correct Traefik labels.'
        ),
        'auto_fix': None,
    },

    # ── Memory / OOM ──────────────────────────────────────────────────
    {
        'regex': re.compile(
            r'JavaScript heap out of memory|MemoryError|'
            r'Cannot allocate memory|OOMKilled|killed.*signal 9',
            re.IGNORECASE
        ),
        'category': 'Out of Memory',
        'severity': 'critical',
        'diagnosis': (
            'The app ran out of memory and was killed. '
            'Increase the memory allocation in Resources settings.'
        ),
        'auto_fix': {
            'resources': {'memory_mb': 'double'},
            'action': 'Doubled memory allocation to prevent OOM',
        },
    },

    # ── Gunicorn / Uvicorn ────────────────────────────────────────────
    {
        'regex': re.compile(
            r'\[CRITICAL\] WORKER TIMEOUT|worker timeout|'
            r'Worker exceeded.*timeout',
            re.IGNORECASE
        ),
        'category': 'Worker Timeout',
        'severity': 'warning',
        'diagnosis': (
            'Workers are timing out. Increase the Gunicorn/Uvicorn timeout '
            'or add more workers.'
        ),
        'auto_fix': {
            'env': {'GUNICORN_TIMEOUT': '120', 'WEB_CONCURRENCY': '2'},
            'action': 'Increased worker timeout and concurrency',
        },
    },
]


def diagnose_runtime_logs(
    logs: str,
    service=None,
    deployment=None,
    auto_apply: bool = True,
) -> list[dict]:
    """
    Scan runtime logs for known error patterns and return diagnoses.

    Returns a list of dicts:
      {category, severity, diagnosis, action_taken, auto_fixed}
    """
    if not logs:
        return []

    results = []

    for pattern in ERROR_PATTERNS:
        regex = cast("re.Pattern[str]", pattern['regex'])
        match = regex.search(logs)
        if not match:
            continue

        result = {
            'category': pattern['category'],
            'severity': pattern['severity'],
            'diagnosis': pattern['diagnosis'],
            'action_taken': None,
            'auto_fixed': False,
        }

        fix = pattern.get('auto_fix')
        if fix and auto_apply and service:
            try:
                action = _apply_fix(cast(dict, fix), match, logs, service, deployment)
                if action:
                    result['action_taken'] = action
                    result['auto_fixed'] = True
            except Exception as e:
                logger.warning(
                    "Auto-fix failed for %s: %s", pattern['category'], e
                )
                result['action_taken'] = f'Auto-fix failed: {e}'

        results.append(result)

    # Log a summary
    if results and deployment:
        crits = sum(1 for r in results if r['severity'] == 'critical')
        fixed = sum(1 for r in results if r['auto_fixed'])
        summary = (
            f"\n🔍 Runtime Diagnosis: {len(results)} issue(s) found "
            f"({crits} critical), {fixed} auto-fixed.\n"
        )
        for r in results:
            icon = '🔴' if r['severity'] == 'critical' else '🟡' if r['severity'] == 'warning' else 'ℹ️'
            summary += f"  {icon} [{r['category']}] {r['diagnosis']}\n"
            if r['action_taken']:
                summary += f"     ✅ {r['action_taken']}\n"

        from apps.deployments.services.pipeline import append_log
        append_log(deployment, summary)

    return results


def _apply_fix(
    fix: dict,
    match: re.Match,
    logs: str,
    service,
    deployment,
) -> str | None:
    """Apply an auto-fix based on the pattern match."""
    import secrets

    # ── Direct env var injection ──
    if 'env' in fix:
        injected = []
        for key, val in fix['env'].items():
            if not EnvironmentVariable.objects.filter(
                service=service, key=key
            ).exists():
                EnvironmentVariable.objects.create(
                    service=service, key=key, value=val, is_secret=False
                )
                injected.append(f'{key}={val}')
        if injected:
            return f"Auto-set: {', '.join(injected)}"
        return None

    # ── Django settings module detection ──
    if 'env_detect' in fix:
        key = fix['env_detect']
        if EnvironmentVariable.objects.filter(service=service, key=key).exists():
            return None

        # Try to detect the project name from the traceback
        project_name = None
        sub_pattern = fix.get('env_pattern')
        if sub_pattern:
            sub_match = sub_pattern.search(logs)
            if sub_match:
                project_name = sub_match.group(1)

        if project_name:
            val = f'{project_name}.settings'
        else:
            val = fix['fallback'].format(project=service.name.replace('-', '_'))

        EnvironmentVariable.objects.create(
            service=service, key=key, value=val, is_secret=False
        )
        return f"Auto-set {key}={val} (detected from traceback)"

    # ── Secret generation ──
    if 'env_generate' in fix:
        for key, length in fix['env_generate'].items():
            if not EnvironmentVariable.objects.filter(
                service=service, key=key
            ).exists():
                val = secrets.token_urlsafe(length)
                EnvironmentVariable.objects.create(
                    service=service, key=key, value=val, is_secret=True
                )
                return f"Auto-generated {key} ({length} chars)"
        return None

    # ── Port detection ──
    if fix.get('port_detect'):
        groups = match.groups()
        port = next((g for g in groups if g), None)
        if port and service:
            current_port = EnvironmentVariable.objects.filter(
                service=service, key='PORT'
            ).first()
            if current_port and current_port.value != port:
                current_port.value = port
                current_port.save()
                return f"Corrected PORT: {current_port.value} → {port}"
            elif not current_port:
                EnvironmentVariable.objects.create(
                    service=service, key='PORT', value=port, is_secret=False
                )
                return f"Auto-set PORT={port} (detected from logs)"
        return None

    # ── Resource boost ──
    if 'resources' in fix:
        res = fix['resources']
        updated = []
        if res.get('memory_mb') == 'double' and service:
            old = service.memory_mb
            service.memory_mb = min(old * 2, 16384)
            service.save(update_fields=['memory_mb'])
            updated.append(f'memory: {old}MB → {service.memory_mb}MB')
        if updated:
            return f"Resource boost: {', '.join(updated)}"
        return None

    return None
