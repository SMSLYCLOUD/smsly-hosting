import shlex

from apps.deployments.models.servers import ManagedServer


def _master_registry_setup_commands() -> list[str]:
    """Commands that make the MASTER registry usable on a node.

    The node pulls platform-built images from the master's registry via
    its routable address (WireGuard mesh IP or public IP). Docker on the
    node needs:
      1. The registry's self-signed TLS cert in /etc/docker/certs.d/
         (installed by lib/docker.sh during node provisioning; this is a
         safety net for nodes provisioned before that flow existed).
      2. A docker login with the platform registry credentials.

    Returns the shell command list (empty when the master registry URL
    or credentials are not resolvable — single-host installs with no
    remote nodes skip this).
    """
    commands: list[str] = []
    try:
        from apps.deployments.services.registry_routing import master_registry_node_url
        node_url = master_registry_node_url()
        if not node_url:
            return commands

        # Registry creds: PlatformConfig holds the htpasswd-matching pair.
        from apps.deployments.models.core import PlatformConfig
        user = (PlatformConfig.get_config_value("registry_user") or "smsly-registry").strip()
        pwd = (PlatformConfig.get_config_value("registry_password") or "").strip()
        if user and pwd:
            safe_user = shlex.quote(user)
            safe_pwd = shlex.quote(pwd)
            safe_url = shlex.quote(node_url)
            commands.append(
                f"printf '%s\\n' {safe_pwd} | docker login --username {safe_user} "
                f"--password-stdin {safe_url} 2>/dev/null || true"
            )
    except Exception:
        return commands
    return commands


def _registry_login_commands(server: ManagedServer) -> str:
    commands = []

    # The master's own registry FIRST — every platform image the node
    # pulls comes from it, scoped access just adds extra registries.
    commands.extend(_master_registry_setup_commands())

    registries = server.registry_access.filter(is_active=True).select_related("content_type")
    for reg in registries:
        url = (reg.registry_url or "").strip()
        if not url:
            continue
        user = (reg.username or "").strip()
        pwd = (reg.password or "").strip()
        if user and pwd:
            safe_user = shlex.quote(user)
            safe_pwd = shlex.quote(pwd)
            safe_url = shlex.quote(url)
            commands.append(
                f"printf '%s\\n' {safe_pwd} | docker login --username {safe_user} "
                f"--password-stdin {safe_url} 2>/dev/null || true"
            )
    if commands:
        return " && ".join(commands)
    return "true"
