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


def _docker_login_all(ssh, server: ManagedServer) -> None:
    """docker login on the node with passwords over the encrypted channel.

    Writes each password to the remote docker-login stdin instead of
    interpolating it into argv (argv is visible in `ps` to anyone on the
    node; the SSH channel is not). Logs only outcomes, never secrets.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    for url, user, pwd in _registry_credential_list(server):
        try:
            import shlex as _shlex
            stdin, stdout, stderr = ssh.exec_command(
                f"docker login --username {_shlex.quote(user)} "
                f"--password-stdin {_shlex.quote(url)} 2>/dev/null || true",
                timeout=60,
            )
            try:
                stdin.write(pwd + "\n")
                stdin.flush()
            finally:
                try:
                    stdin.channel.shutdown_write()
                except Exception:
                    pass
            code = stdout.channel.recv_exit_status()
            if code == 0:
                _logger.info("Node docker login succeeded for %s", url)
            else:
                _logger.warning("Node docker login exited %s for %s", code, url)
        except Exception as exc:
            _logger.warning("Node docker login failed for %s: %s", url, exc)


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
            f"--password-stdin {safe_url} 2>/dev/null || true"
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
