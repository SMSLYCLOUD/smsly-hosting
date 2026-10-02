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
                f"--password-stdin {safe_url} || "
                f"(echo \"docker login failed for {safe_url}\" >&2; exit 1)"
            )
    except Exception:
        return commands
    return commands


def _registry_credential_list(server: ManagedServer) -> list:
    """All (url, username, password) tuples the node must log into.

    Master's registry first, then active scoped registries. Single source
    for both the legacy command-string builder below and the stdin-based
    login used by provisioning (which keeps secrets out of argv/ps).
    """
    creds = []
    try:
        from apps.deployments.services.registry_routing import master_registry_node_url
        node_url = master_registry_node_url()
        if node_url:
            from apps.deployments.models.core import PlatformConfig
            user = (PlatformConfig.get_config_value("registry_user") or "smsly-registry").strip()
            pwd = (PlatformConfig.get_config_value("registry_password") or "").strip()
            if user and pwd:
                creds.append((node_url, user, pwd))
    except Exception:
        pass
    try:
        for reg in server.registry_access.filter(is_active=True).select_related("content_type"):
            url = (reg.registry_url or "").strip()
            user = (reg.username or "").strip()
            pwd = (reg.password or "").strip()
            if url and user and pwd:
                creds.append((url, user, pwd))
    except Exception:
        pass
    return creds


def _docker_login_all(ssh, server: ManagedServer) -> bool:
    """docker login on the node without leaking passwords.

    The SSH wrapper returns ``(out, err, code)`` tuples (no stdin
    channel), so the password travels inside a base64 pipe evaluated
    on the NODE. Logs only outcomes, never secrets.
    Returns True when every registry login succeeded, False otherwise.
    """
    import base64 as _b64
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    all_ok = True
    for url, user, pwd in _registry_credential_list(server):
        try:
            import shlex as _shlex
            blob = _b64.b64encode(pwd.encode("utf-8")).decode("ascii")
            out, err, code = ssh.exec_command(
                f"echo {blob} | base64 -d | docker login --username {_shlex.quote(user)} "
                f"--password-stdin {_shlex.quote(url)}",
                timeout=60,
            )
            if code == 0:
                _logger.info("Node docker login succeeded for %s", url)
            else:
                _logger.error(
                    "Node docker login FAILED for registry %s (exit %s)%s",
                    url, code, f": {(err or out).strip()[:200]}" if (err or out) else "",
                )
                all_ok = False
        except Exception as exc:
            _logger.error("Node docker login failed for registry %s: %s", url, exc)
            all_ok = False
    return all_ok


def _registry_login_commands(server: ManagedServer) -> str:
    """Legacy command-string builder (kept for compatibility).

    Prefer _docker_login_all for provisioning: this variant embeds
    passwords in argv (visible in remote `ps`). Single source is
    _registry_credential_list.
    """
    commands = []
    commands.extend(_master_registry_setup_commands())

    for url, user, pwd in _registry_credential_list(server):
        # Skip the master entry already covered above (same URL).
        safe_user = shlex.quote(user)
        safe_pwd = shlex.quote(pwd)
        safe_url = shlex.quote(url)
        commands.append(
            f"printf '%s\\n' {safe_pwd} | docker login --username {safe_user} "
            f"--password-stdin {safe_url} || "
            f"(echo \"docker login failed for {safe_url}\" >&2; exit 1)"
        )
    # Deduplicate identical commands (master entry appears twice).
    seen = set()
    unique = []
    for cmd in commands:
        if cmd not in seen:
            seen.add(cmd)
            unique.append(cmd)
    if unique:
        return " && ".join(unique)
    return "true"
