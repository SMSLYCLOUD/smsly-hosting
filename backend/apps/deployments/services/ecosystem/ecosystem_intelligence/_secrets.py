import logging

logger = logging.getLogger(__name__)


def _unify_cross_service_secrets(services: list[dict]) -> None:
    """Map well-known cross-service secret patterns to shared secret keys."""
    _SUFFIX_TO_KEY: dict[str, str] = {
        "_TO_AUDIT_SECRET": "audit_service_secret",
        "_TO_BACKEND_SECRET": "backend_secret",
        "_TO_IDENTITY_SECRET": "identity_secret",
        "_TO_PLATFORM_SECRET": "platform_secret",
        "_TO_POLICY_SECRET": "policy_secret",
        "_TO_RATE_LIMIT_SECRET": "ratelimit_secret",
        "_TO_GATEWAY_SECRET": "gateway_secret",
        "_TO_SECURITY_GATEWAY_SECRET": "gateway_secret",
        "_TO_RATELIMIT_SECRET": "ratelimit_secret",
    }
    _FULLNAME_TO_KEY: dict[str, str] = {
        "BACKEND_SECRET": "backend_secret",
        "PLATFORM_API_SECRET": "platform_api_secret",
        "RATELIMIT_SECRET": "ratelimit_secret",
        "IDENTITY_SECRET": "identity_secret",
        "IDENTITY_SERVICE_SECRET": "service_secret",
        "GATEWAY_SECRET": "gateway_secret",
    }

    def _infer_prefixes_for_svc(env_map: dict) -> list[str]:
        keys_upper = [k.upper() for k in env_map]
        counts: dict[str, int] = {}
        for k in keys_upper:
            parts = k.split("_")
            for i in range(1, len(parts)):
                p = "_".join(parts[:i]) + "_"
                counts[p] = counts.get(p, 0) + 1
        return [p for p, c in counts.items() if c >= 3]

    for svc in services:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        svc_prefixes = svc.get("_env_prefixes", [])
        if not svc_prefixes:
            svc_prefixes = _infer_prefixes_for_svc(env_map)
        for key in list(env_map.keys()):
            val = str(env_map.get(key, ""))
            if val.startswith("{{SHARED_SECRET:"):
                continue
            key_u = key.upper()
            matched_key = None
            for suffix, shared_key in _SUFFIX_TO_KEY.items():
                if key_u.endswith(suffix):
                    matched_key = shared_key
                    break
            if not matched_key:
                for full_name, shared_key in _FULLNAME_TO_KEY.items():
                    if key_u == full_name:
                        matched_key = shared_key
                        break
            if not matched_key:
                # Try stripping known prefix then matching suffix
                for prefix in svc_prefixes:
                    pu = prefix.upper()
                    if key_u.startswith(pu) and len(key_u) > len(pu):
                        stripped = key_u[len(pu):]
                        for suffix, shared_key in _SUFFIX_TO_KEY.items():
                            if stripped.endswith(suffix):
                                matched_key = shared_key
                                break
                        if matched_key:
                            break
            if matched_key:
                env_map[key] = f"{{{{SHARED_SECRET:{matched_key}}}}}"
                logger.info(
                    "Step 3d: Cross-service %s/%s -> {{SHARED_SECRET:%s}}",
                    svc.get("name", "?"), key, matched_key,
                )


def _unify_same_name_secrets(services: list[dict]) -> None:
    """Unify same-named secrets across services to a single shared key."""
    key_vals: dict[str, dict[str, str]] = {}
    for svc in services:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        for k, v in env_map.items():
            key_vals.setdefault(k, {})[svc.get("name", "?")] = str(v or "")

    for k, svc_vals in key_vals.items():
        if len(svc_vals) < 2:
            continue
        has_secret = any(
            any(w in k.upper() for w in ["SECRET", "KEY", "TOKEN", "PASSWORD"])
            for _ in svc_vals
        )
        if not has_secret:
            continue
        shared_keys = set()
        real_vals = set()
        for v in svc_vals.values():
            if v.startswith("{{SHARED_SECRET:"):
                sk = v.split("{{SHARED_SECRET:")[-1].rstrip("}}").rstrip("}")
                shared_keys.add(sk)
            elif v.startswith("{{") or v.startswith("REPLACE_") or v == "":
                continue
            else:
                real_vals.add(v)
        if not shared_keys and len(real_vals) <= 1:
            continue
        target_key = (
            shared_keys.pop()
            if len(shared_keys) == 1
            else k.lower()
        )
        for svc_name, v in svc_vals.items():
            if v != f"{{{{SHARED_SECRET:{target_key}}}}}":
                for svc in services:
                    if svc.get("name") == svc_name:
                        svc["env_vars"][k] = f"{{{{SHARED_SECRET:{target_key}}}}}"
                        break
