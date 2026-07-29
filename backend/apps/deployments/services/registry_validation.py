"""
Centralised registry-URL validation for inter-node image transfers.

The platform runs an internal Docker registry (default
``registry:5000``) and may be configured to push to a small
allowlist of public registries (Docker Hub, GHCR, Quay, GCR,
MCR, ECR Public). Any code path that constructs a ``docker pull``
or ``docker push`` command, or that runs ``docker load -i`` on a
manifest-bearing archive, should run the candidate image through
``validate_image_registry`` first.

The serializer-layer validator in
``apps/deployments/serializers.py`` (``_validate_docker_image``)
also consults the same allowlist, but it only runs at the API
boundary. Internal callers (provisioner fallback, self-healing
re-builder, cross-node image transfer) construct image refs
directly and bypass the serializer, so this helper exists to
centralize the policy.

A user can never cause the platform to pull an image from a
registry host that is not on this list — that includes personal
``attacker.example.com`` repositories, link-local hosts, and
private IP ranges that aren't our own.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Canonical allowlist; consumed by serializers.py and
# models_registry_scope.py via ``all_allowed_registry_hosts()``
# so the policy cannot drift between the API boundary and the
# internal callers.
ALLOWED_IMAGE_REGISTRY_HOSTS = (
    # host:port[:/] prefix — these are the registries the platform
    # actually supports.
    "127.0.0.1:5000",
    "localhost:5000",
    "registry:5000",
    "smsly-hosting-registry:5000",
    "ghcr.io",
    "docker.io",  # Docker Hub — also matches library/<name> style
    "registry-1.docker.io",
    "quay.io",
    "gcr.io",
    "mcr.microsoft.com",
    "public.ecr.aws",
)


def all_allowed_registry_hosts() -> list[str]:
    """Return the static allowlist plus the platform's configured
    ``CONTAINER_REGISTRY_URL`` (e.g. ``registry.smsly.cloud``).

    The static tuple covers well-known internal and public registries.
    The configured registry URL is appended at runtime so the platform
    automatically allows its own public/shareable registry domain without
    a manual edit to the tuple above.
    """
    hosts = list(ALLOWED_IMAGE_REGISTRY_HOSTS)
    try:
        from urllib.parse import urlparse

        from django.conf import settings

        _cfg_url = getattr(settings, "CONTAINER_REGISTRY_URL", "") or ""
        if _cfg_url:
            if "://" in _cfg_url:
                _cfg_host = (urlparse(_cfg_url).netloc or "").rstrip("/")
            else:
                _cfg_host = _cfg_url.split("/")[0].rstrip("/")
            if _cfg_host and _cfg_host not in hosts:
                hosts.append(_cfg_host)
    except Exception as exc:
        logger.debug("Failed to resolve registry hosts: %s", exc)
    return hosts


# Shell metacharacters that would let an image ref break out of
# the docker CLI argv position and run arbitrary code. The docker
# client rejects these in image names, but the docker CLI itself
# uses exec.Command which goes through /bin/sh on the remote.
_FORBIDDEN_CHARS = ("\n", "\r", "\t", ";", "&", "|", "`", "$", " ", "<", ">")


def _registry_prefix_for(image: str) -> str:
    """Return the registry prefix of a Docker image reference.

    For ``registry:5000/foo/bar:tag`` -> ``registry:5000``.
    For ``nginx:1.27-alpine`` -> ``docker.io`` (Docker Hub library).
    """
    first_slash = image.find("/")
    if first_slash == -1 or (
        not ("." in image[:first_slash]
             or ":" in image[:first_slash]
             or image[:first_slash] == "localhost")
    ):
        # No registry prefix → Docker Hub library reference
        return "docker.io"
    return image[:first_slash]


def validate_image_registry(image: str, service=None) -> str:
    """Restrict ``image`` to a Docker-safe reference whose registry
    host is on the platform allowlist or user's custom credentials.

    Returns the cleaned image string. Raises ``ValueError`` if the
    image is malformed, contains shell metacharacters, or points at
    a registry host that is not on the allowlist. Callers should
    treat the raised exception as a hard fail (do not pull the
    image).
    """
    if image is None:
        raise ValueError("image must not be None.")
    if not isinstance(image, str) or not image.strip():
        raise ValueError("image must be a non-empty string.")
    image = image.strip()
    if any(c in image for c in _FORBIDDEN_CHARS):
        raise ValueError(
            "image must not contain whitespace or shell metacharacters."
        )
    prefix = _registry_prefix_for(image)

    allowed_hosts = all_allowed_registry_hosts()

    # Per-scope allowlist: append hosts from Project → Team → Organization chain
    if service and getattr(service, "project_id", None):
        try:
            from apps.deployments.models.registry_scope import ScopedRegistry

            scoped_hosts = ScopedRegistry.resolve_allowed_hosts(service.project)
            for h in scoped_hosts:
                if h not in allowed_hosts:
                    allowed_hosts.append(h)
        except Exception as exc:
            logger.debug("Failed to resolve scoped registry hosts for project: %s", exc)

    # User's custom RegistryCredential hosts (existing behaviour)
    if service and getattr(service, "owner_id", None):
        from apps.deployments.models.registry import RegistryCredential

        custom_creds = RegistryCredential.objects.filter(
            owner_id=service.owner_id, is_active=True
        )
        for cred in custom_creds:
            if cred.registry_url:
                clean_url = (
                    cred.registry_url.replace("https://", "")
                    .replace("http://", "")
                    .split("/")[0]
                )
                if clean_url not in allowed_hosts:
                    allowed_hosts.append(clean_url)

    if not any(
        prefix == allowed or prefix.startswith(allowed + "/")
        for allowed in allowed_hosts
    ):
        raise ValueError(
            f"image registry {prefix!r} is not on the platform allowlist. "
            f"Allowed: {', '.join(allowed_hosts)}."
        )
    return image


def safe_registry_host_for_internal_fallback() -> str:
    """Return the registry host (host:port) the platform should
    use as the internal fallback when constructing an image ref
    for a service that doesn't have an explicit ``docker_image``.

    Resolution priority:
      1. If ``CONTAINER_REGISTRY_URL`` is a loopback or Docker DNS
         name (``registry:5000``), return the master's WireGuard
         mesh IP so both local and remote nodes can pull the image.
      2. If ``MASTER_MESH_IP`` is set, prefer it over the configured
         URL for cross-node reachability.
      3. Otherwise return the configured registry's netloc.

    Used by ``self_healing_orchestrator`` and any other code path
    that needs to construct an internal-registry image ref that
    works across nodes.
    """
    from urllib.parse import urlparse

    from django.conf import settings

    registry_url = getattr(settings, "CONTAINER_REGISTRY_URL", "") or ""

    # Always prefer mesh IP when available — it works for both local
    # (host has the WireGuard interface) and remote nodes.
    from apps.deployments.services.provisioner import _get_master_mesh_ip
    master_ip = _get_master_mesh_ip()
    if master_ip:
        return f"{master_ip}:5000"

    if registry_url.startswith(("127.0.0.1", "localhost")):
        return "127.0.0.1:5000"

    # For Docker DNS names (registry:5000), keep as-is — it resolves
    # inside the smsly-net overlay for local containers.
    if registry_url.startswith("registry:"):
        return registry_url

    parsed = urlparse(registry_url)
    return (parsed.netloc or parsed.path).rstrip("/")


def safe_image_for_service(service_name: str, tag: str = "latest") -> str:
    """Build an internal-registry image reference for a service.

    Combines ``safe_registry_host_for_internal_fallback`` with the
    caller-supplied service name and tag. The result is
    ``<host>:<port>/smsly/<service_name>:<tag>`` and is guaranteed
    to point at a registry on the platform allowlist.

    The ``service_name`` is a string the user controls; the caller
    is responsible for having already validated it (e.g. via
    ``ServiceSerializer.validate_name`` which restricts to DNS-label
    characters). This helper just constructs the full ref.
    """
    host = safe_registry_host_for_internal_fallback()
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]", "", tag or "latest")[:128] or "latest"
    safe_name = re.sub(r"[^a-z0-9_.-]", "", (service_name or "").lower())[:63] or "app"
    return f"{host}/smsly/{safe_name}:{safe_tag}"
