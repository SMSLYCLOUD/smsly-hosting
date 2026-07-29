import shlex

from apps.deployments.models.servers import ManagedServer


def _registry_login_commands(server: ManagedServer) -> str:
    commands = []
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
