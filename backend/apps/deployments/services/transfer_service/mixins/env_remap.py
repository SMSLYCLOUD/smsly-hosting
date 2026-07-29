import json
import logging
import re
import shlex

from ..helpers import _safe_service_name

logger = logging.getLogger(__name__)


class EnvRemapMixin:
    def _remap_target_platform_env(self, backend_container=None):
        if not self.transfer.service:
            return

        service_name = _safe_service_name(self.transfer.service.name)
        payload = {'service_name': service_name}
        remap_code = """
import json
import os
import socket
from urllib.parse import urlparse
from apps.deployments.models import Service, EnvironmentVariable

payload = json.loads(%r)
svc = Service.objects.filter(name=payload["service_name"]).first()
platform_database_url = os.environ.get("DATABASE_URL", "").strip()
platform_redis_url = os.environ.get("REDIS_URL", "").strip()
pre_transfer = {}
if svc:
    url_remaps = {
        "DATABASE_URL": platform_database_url,
        "MARKETER_DATABASE_URL": platform_database_url,
        "REDIS_URL": platform_redis_url,
        "RATE_LIMIT_REDIS_URL": platform_redis_url,
        "CACHE_URL": platform_redis_url,
        "CELERY_BROKER_URL": platform_redis_url,
        "CELERY_RESULT_BACKEND": platform_redis_url,
    }
    target_domain = os.environ.get("DOMAIN", "").strip()
    domain_remaps = {}
    if target_domain:
        domain_remaps = {
            "PUBLIC_DOMAIN": target_domain,
            "ALLOWED_HOSTS": f"{target_domain},localhost,127.0.0.1",
            "DJANGO_ALLOWED_HOSTS": target_domain,
            "SITE_URL": f"https://{target_domain}",
        }

    for candidate_key in list(url_remaps.keys()) + list(domain_remaps.keys()):
        env = EnvironmentVariable.objects.filter(service=svc, key=candidate_key).first()
        if env is not None:
            pre_transfer[candidate_key] = str(env.value or "")

    for dk, dv in domain_remaps.items():
        env = EnvironmentVariable.objects.filter(service=svc, key=dk).first()
        if env and env.value and str(env.value).strip():
            old_val = str(env.value).strip()
            old_base = os.environ.get("DOMAIN_OLD", "").strip() or "localhost"
            if old_base in old_val or old_val == "********":
                EnvironmentVariable.objects.update_or_create(
                    service=svc, key=dk,
                    defaults={"value": dv, "source": "SYSTEM"},
                )

    for key, replacement_url in url_remaps.items():
        if not replacement_url:
            continue
        env = EnvironmentVariable.objects.filter(service=svc, key=key).first()
        value = str(env.value or "").strip() if env else ""
        parsed = urlparse(value)
        host = parsed.hostname
        should_remap = value == "********"
        if host and host not in {"redis", "localhost", "127.0.0.1"}:
            try:
                socket.getaddrinfo(host, parsed.port or 6379)
            except OSError:
                should_remap = True
        if should_remap:
            EnvironmentVariable.objects.update_or_create(
                service=svc,
                key=key,
                defaults={"value": replacement_url, "is_secret": True, "source": "SYSTEM"},
            )

print("PRE_TRANSFER_ENV_JSON_BEGIN")
print(json.dumps(pre_transfer))
print("PRE_TRANSFER_ENV_JSON_END")
""".strip() % json.dumps(payload)

        pre_transfer: dict = {}
        try:
            exec_result = self._exec_on_target(remap_code)
            output = exec_result.get('stdout', '')
            match = re.search(
                r"PRE_TRANSFER_ENV_JSON_BEGIN\s*(\{.*?\})\s*PRE_TRANSFER_ENV_JSON_END",
                output,
                re.DOTALL,
            )
            if match:
                try:
                    pre_transfer = json.loads(match.group(1)) or {}
                except json.JSONDecodeError as exc:
                    logger.warning("Could not parse pre-transfer env snapshot: %s", exc)
        except Exception as exc:
            logger.warning("Failed to remap target platform env vars: %s", exc)

        if pre_transfer:
            metadata = dict(self.transfer.metadata or {})
            metadata['pre_transfer_env_vars'] = pre_transfer
            self.transfer.metadata = metadata
            self.transfer.save(update_fields=['metadata'])

    def _revert_target_platform_env(self):
        if self.transfer.transfer_type != 'SERVICE' or not self.transfer.service:
            return
        pre_transfer = (self.transfer.metadata or {}).get('pre_transfer_env_vars') or {}
        if not pre_transfer:
            return

        try:
            backend_container = self._find_remote_backend_container(required=False)
        except Exception as exc:
            self._log(f"Could not locate backend container for env revert: {exc}")
            return
        if not backend_container:
            self._log("Backend container not found on target — skipping env revert.")
            return

        service_name = _safe_service_name(self.transfer.service.name)
        shlex.quote(backend_container)
        script_path = f"/tmp/transfer_revert_env_{self.transfer.id}.py"
        shlex.quote(script_path)

        payload = {
            'service_name': service_name,
            'pre_transfer': pre_transfer,
        }
        revert_code = f"""
import json
import os
import sys
import django

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable

payload = json.loads({json.dumps(payload)})
svc = Service.objects.filter(name=payload['service_name']).first()
if svc:
    for key, value in payload['pre_transfer'].items():
        EnvironmentVariable.objects.update_or_create(
            service=svc,
            key=key,
            defaults={{
                'value': value,
                'is_secret': True,
                'source': 'SYSTEM',
            }},
        )
    print(f"REVERTED {{len(payload['pre_transfer'])}} env vars for {{payload['service_name']}}")
else:
    print('ERROR: service not found', file=sys.stderr)
"""
        try:
            exec_result = self._exec_on_target(revert_code)
            output = exec_result.get('stdout', '')
            if "REVERTED" in output:
                self._log(f"Reverted target platform env vars: {output.strip()}")
            else:
                self._log(f"Target env revert did not confirm: {output.strip()[:300]}")
        except Exception as exc:
            logger.warning("Failed to revert target platform env vars: %s", exc)
