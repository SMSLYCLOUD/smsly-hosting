"""
Autoscaler container classification registry.

External apps can extend the classification logic by registering
a callable that receives a container name and returns a tuple
(svc_type, app_name) or None if the container should be ignored.
"""

# Global list of registered classifiers
_CLASSIFIERS: list = []


def register_classifier(func):
    """
    Decorator to register a custom classifier.

    The registered function must accept a container name (str) and
    return a tuple (svc_type, app_name) or None to skip.
    """
    if not callable(func):
        raise TypeError("Classifier must be a callable")
    if func not in _CLASSIFIERS:
        _CLASSIFIERS.append(func)
    return func


def classify(name: str):
    """
    Dispatch to registered classifiers; fall back to the built-in
    _builtin_classify logic.
    """
    for fn in _CLASSIFIERS:
        try:
            result = fn(name)
            if result is not None:
                return result
        except Exception:
            continue

    return _builtin_classify(name)


# ---------------------------------------------------------------------------
# Built-in classifier (kept private)
# ---------------------------------------------------------------------------

def _builtin_classify(name: str):
    """
    Default classification logic used when no custom classifier matches.
    Handles both Docker container names and K8s pod names.

    Returns:
        tuple: (svc_type, app_name) or None if the container is infrastructure.
    """
    INFRA_PREFIXES = (
        "smsly-hosting-db",
        "smsly-hosting-redis",
        "smsly-hosting-traefik",
        "smsly-hosting-pgcat",
        "smsly-hosting-registry",
        "smsly-hosting-socket-proxy",
        "smsly-hosting-route-fallback",
        "smsly-hosting-postgresql",
        "smsly-hosting-caddy",
        "smsly-system",
    )

    if any(name.startswith(p) for p in INFRA_PREFIXES):
        return None

    # K8s pod naming: <deployment>-<replicaset>-<hash>
    # Strip the trailing hash to get the deployment name
    k8s_parts = name.rsplit("-", 2)
    if len(k8s_parts) == 3 and len(k8s_parts[-1]) >= 5:
        base = k8s_parts[0]
        if "celery-beat" in base:
            return "celery", "platform"
        if "celery" in base:
            return "celery", "platform"
        if "backend" in base:
            return "gunicorn", "platform"
        if "frontend" in base:
            return "gunicorn", "platform"

    if "celery-beat" in name:
        return "celery", "platform"
    if "celery" in name:
        return "celery", "platform"
    if "backend" in name and "smsly-hosting" in name:
        return "gunicorn", "platform"
    if "frontend" in name and "smsly-hosting" in name:
        return "gunicorn", "platform"

    # Customer apps – use the part before the first dash or the whole name
    return "gunicorn", name.split("-", maxsplit=1)[0] if "-" in name else name
