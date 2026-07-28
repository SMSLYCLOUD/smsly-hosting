import json
import logging

from .helpers import _repo_short_name

logger = logging.getLogger(__name__)


def _ai_env_crosscheck(services: list[dict], ai_provider: str | None) -> None:
    from apps.intelligence.providers import _cached_ask

    if not ai_provider:
        return

    _prefix_map: dict[str, list[str]] = {}
    for svc in services:
        prefixes = svc.get("_env_prefixes", [])
        if prefixes:
            _prefix_map[_repo_short_name(svc)] = prefixes

    lines = []
    if _prefix_map:
        lines.append("ENV PREFIX DETECTED (services using pydantic env_prefix):")
        for svc_name, prefixes in _prefix_map.items():
            lines.append(f"  {svc_name} prefixes: {', '.join(prefixes)}")
        lines.append("")

    for svc in services:
        name = _repo_short_name(svc)
        env = svc.get("env_vars", {})
        secrets = {k: v for k, v in env.items() if any(w in k.upper() for w in ["SECRET", "KEY", "TOKEN", "PASSWORD", "SALT"])}
        urls = {k: v for k, v in env.items() if any(w in k.upper() for w in ["_URL", "_ENDPOINT", "_HOST", "_API"])}
        lines.append(f"SERVICE: {name}")
        for k, v in sorted(secrets.items()):
            lines.append(f"  SECRET  {k}: {v}")
        for k, v in sorted(urls.items()):
            lines.append(f"  URL     {k}: {v}")
        lines.append("")

    prompt = f"""You are auditing a microservice deployment plan. Review the env vars below.

Identify:
1. Secrets with DIFFERENT names across services that must hold the SAME value
   (e.g. POLICY_TO_AUDIT_SECRET on policy-service ↔ AUDIT_SERVICE_SECRET on audit-service)
2. Prefixed secrets (e.g. RATE_LIMIT_GATEWAY_SECRET) that should match unprefixed
   counterparts (GATEWAY_SECRET) on other services
3. Empty required secrets that should use {{GENERATE}} or {{SHARED_SECRET:name}}
4. Empty service URLs that should use {{SERVICE:name}}

{''.join(lines)}

Return ONLY valid JSON:
{{"corrections": [
  {{"service": "service-name", "var": "VAR", "new_value": "{{SHARED_SECRET:name}}"}}
]}}"""

    logger.info("=== AI ENV CROSS-CHECK ===")
    try:
        resp, provider = _cached_ask(
            prompt, system_prompt="You are a DevOps auditor. Return ONLY valid JSON.", provider_id=ai_provider,
        )
        resp = resp or ""
        start = resp.find('{')
        end = resp.rfind('}')
        if start == -1 or end == -1:
            return
        result = json.loads(resp[start:end+1])
        corrections = result.get("corrections") or []
        if not corrections:
            logger.info("AI cross-check: no corrections needed")
            return
        logger.info(f"AI cross-check found {len(corrections)} corrections")
        for corr in corrections:
            svc_name = str(corr.get("service", ""))
            var_name = str(corr.get("var", ""))
            new_val = str(corr.get("new_value", ""))
            if not svc_name or not var_name or not new_val:
                continue
            for svc in services:
                if (_repo_short_name(svc) == svc_name) and var_name in svc.get("env_vars", {}):
                    svc["env_vars"][var_name] = new_val
                    logger.info(f"  Fixed {svc_name}/{var_name} → {new_val}")
                    break
    except Exception as e:
        logger.warning(f"AI env cross-check failed: {e}")
