"""
Server auto-provisioning service.

SSHes into a fresh VPS, uploads and runs install.sh,
then auto-registers the server with the API credentials.
"""

import io
import ipaddress
import logging
import os
import re
import tarfile
import tempfile
import time
from datetime import timedelta
from urllib.parse import quote

import paramiko
import requests
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction
from django.utils import timezone

from apps.deployments.models_servers import ManagedServer

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


PROVISION_TIMEOUT_SECONDS = _env_int(
    "SMSLY_PROVISION_TIMEOUT_SECONDS",
    1800,
    minimum=60,
)


def _source_root_dir() -> str:
    """Return container path to smsly-hosting source root."""
    mounted_root = os.environ.get("SMSLY_PROVISION_SOURCE_ROOT", "/platform-src")
    if mounted_root and os.path.isdir(mounted_root):
        return os.path.abspath(mounted_root)
    # Fallback to backend container project root when full source is unavailable.
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))


def _build_local_source_bundle() -> str:
    """
    Build a temporary tar.gz bundle of source code for tokenless provisioning.

    Returns local temporary file path.
    """
    source_root = _source_root_dir()
    if not os.path.isdir(source_root):
        raise FileNotFoundError(f"Source root not found: {source_root}")

    fd, archive_path = tempfile.mkstemp(prefix="smsly-src-", suffix=".tar.gz")
    os.close(fd)

    excluded = {
        ".git",
        "node_modules",
        ".next",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        ".credentials",
        ".git-credentials",
    }

    with tarfile.open(archive_path, mode="w:gz") as tar:
        for root, dirs, files in os.walk(source_root, topdown=True):
            dirs[:] = [d for d in dirs if d not in excluded]
            rel_root = os.path.relpath(root, source_root)
            rel_root = "" if rel_root == "." else rel_root

            for filename in files:
                if filename in excluded:
                    continue
                local_path = os.path.join(root, filename)
                rel_path = os.path.join(rel_root, filename) if rel_root else filename
                try:
                    tar.add(local_path, arcname=rel_path, recursive=False)
                except (PermissionError, FileNotFoundError, OSError):
                    # Skip unreadable/transient files in host-mounted source root.
                    continue

    return archive_path


def _is_github_token_known_invalid(token: str | None) -> bool:
    """
    Return True when GitHub explicitly reports token is invalid/revoked.
    """
    if not token:
        return False
    try:
        response = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
    except requests.RequestException:
        # Network/transient failures should not force fallback.
        return False
    return response.status_code == 401


def _load_install_script():
    """
    Load the installer script content.

    Priority:
    1) Local file in the backend image/workdir (for bundled installs)
    2) Fallback to GitHub raw URL (for minimal backend images)
    """
    candidates = [
        # /app/install.sh if bundled into the backend container
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../install.sh")
        ),
        os.path.abspath(os.path.join(os.getcwd(), "install.sh")),
    ]

    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as install_file:
                return install_file.read(), f"local:{path}"

    script_url = (
        os.environ.get(
            "SMSLY_INSTALL_SCRIPT_URL",
            "https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh",
        )
        .strip()
    )
    response = requests.get(script_url, timeout=30)
    response.raise_for_status()
    content = response.text
    if not content.strip():
        raise ValueError("Downloaded installer script is empty")
    return content, f"url:{script_url}"


def _inject_repo_clone_auth(script_content: str, github_token: str | None):
    """Inject GitHub auth into installer clone URL for private repos."""
    if not github_token:
        return script_content, False

    encoded = quote(github_token, safe="")
    auth_url = (
        f"https://x-access-token:{encoded}@github.com/"
        "SMSLYCLOUD/smsly-hosting.git"
    )
    pattern = (
        r'git clone https://github\.com/SMSLYCLOUD/smsly-hosting\.git "\$INSTALL_DIR"'
    )
    replaced = re.sub(
        pattern,
        f'git clone {auth_url} "$INSTALL_DIR"',
        script_content,
        count=1,
    )
    return replaced, replaced != script_content


def _broadcast_provision_log(server: ManagedServer, message: str):
    """Push a provision log line via WebSocket."""
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"provision_{server.id}",
            {
                "type": "provision.log",
                "message": message,
            },
        )
    except Exception:
        pass  # WebSocket is optional — logs are still saved to DB


def _append_log(server: ManagedServer, line: str):
    """Append a line to provision_logs and broadcast."""
    server.provision_logs += line + "\n"
    server.save(update_fields=["provision_logs", "updated_at"])
    _broadcast_provision_log(server, line)


def _get_ssh_client(server: ManagedServer) -> paramiko.SSHClient:
    """Create and connect an SSH client to the target server."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()

    # Zero-click provisioning default: accept first connection host key.
    # Set SMSLY_STRICT_SSH_HOST_KEY_CHECK=true to enforce known_hosts pinning.
    strict_host_key_check = str(
        os.environ.get("SMSLY_STRICT_SSH_HOST_KEY_CHECK", "false")
    ).strip().lower() in ("1", "true", "yes", "on")
    if strict_host_key_check:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": server.host,
        "port": server.ssh_port,
        "username": server.ssh_user,
        "timeout": 30,
        "banner_timeout": 30,
    }

    if server.ssh_key:
        # Use SSH private key
        key_file = io.StringIO(server.ssh_key)
        try:
            pkey = paramiko.RSAKey.from_private_key(key_file)
        except paramiko.SSHException:
            key_file.seek(0)
            pkey = paramiko.Ed25519Key.from_private_key(key_file)
        connect_kwargs["pkey"] = pkey
    elif server.ssh_password:
        connect_kwargs["password"] = server.ssh_password
    else:
        raise ValueError("No SSH credentials provided (need password or key)")

    client.connect(**connect_kwargs)
    return client


@shared_task(bind=True, max_retries=0, soft_time_limit=1860, time_limit=1920)
def provision_server(self, server_id: str):
    """
    Provision CloudNeuron on a remote server via SSH.

    Steps:
    1. SSH into the target server
    2. Upload install.sh
    3. Run it in non-interactive mode
    4. Parse output for credentials
    5. Update ManagedServer with api_url + api_token
    """
    try:
        with transaction.atomic():
            server = ManagedServer.objects.select_for_update().get(id=server_id)
            conflict = (
                ManagedServer.objects.select_for_update()
                .filter(
                    host=server.host,
                    provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
                )
                .exclude(id=server.id)
                .exists()
            )
            if conflict:
                server.provision_status = ManagedServer.ProvisionStatus.FAILED
                server.provision_logs = (
                    "Another provisioning task is already running for this host. "
                    "Retry after the active install completes.\n"
                )
                server.save(
                    update_fields=["provision_status", "provision_logs", "updated_at"]
                )
                return
            # Mark provisioning while holding locks to reduce same-host races.
            server.provision_status = ManagedServer.ProvisionStatus.PROVISIONING
            server.provision_logs = ""
            server.save(
                update_fields=["provision_status", "provision_logs", "updated_at"]
            )
    except ManagedServer.DoesNotExist:
        logger.error("Server %s not found", server_id)
        return

    _append_log(server, "🚀 Starting CloudNeuron provisioning...")
    _append_log(server, f"📡 Connecting to {server.ssh_user}@{server.host}:{server.ssh_port}")

    ssh = None
    local_bundle_path = None
    try:
        github_token = None
        try:
            from apps.deployments.utils import get_github_oauth_token_for_user
            github_token = get_github_oauth_token_for_user(server.owner)
        except Exception as token_exc:  # pragma: no cover
            logger.debug("Could not resolve GitHub token for provisioning: %s", token_exc)
        prefer_local_bundle = str(
            os.environ.get("SMSLY_PROVISION_USE_LOCAL_BUNDLE", "true")
        ).strip().lower() not in ("0", "false", "no", "off")
        token_known_invalid = (
            _is_github_token_known_invalid(github_token)
            if (github_token and not prefer_local_bundle)
            else False
        )
        use_local_bundle = prefer_local_bundle or (not github_token) or token_known_invalid

        # ── Step 1: Connect ──
        ssh = _get_ssh_client(server)
        _append_log(server, "✅ SSH connection established")

        # ── Step 2: Upload install script ──
        _append_log(server, "📦 Uploading install script...")
        sftp = ssh.open_sftp()

        install_script_content, install_script_source = _load_install_script()
        injected_auth = False
        if github_token and not token_known_invalid and not use_local_bundle:
            install_script_content, injected_auth = _inject_repo_clone_auth(
                install_script_content,
                github_token,
            )
        _append_log(server, f"📥 Installer source: {install_script_source}")
        if injected_auth:
            _append_log(server, "🔐 Using linked GitHub token for installer repository clone.")
        elif use_local_bundle and prefer_local_bundle:
            _append_log(
                server,
                "ℹ️ Provisioning in local-bundle mode (no GitHub clone required).",
            )
        elif token_known_invalid:
            _append_log(
                server,
                "⚠️ Linked GitHub token appears invalid; using local source bundle fallback.",
            )
        elif not github_token:
            _append_log(
                server,
                "ℹ️ No linked GitHub token found; using local source bundle fallback.",
            )
        remote_script = sftp.open("/tmp/smsly-install.sh", "w")
        try:
            remote_script.write(install_script_content)
            remote_script.flush()
        finally:
            remote_script.close()
        sftp.chmod("/tmp/smsly-install.sh", 0o755)

        run_prefix = ""
        if use_local_bundle:
            _append_log(server, "📦 Uploading local source bundle for provisioning fallback...")
            local_bundle_path = _build_local_source_bundle()
            sftp.put(local_bundle_path, "/tmp/smsly-hosting-src.tar.gz")
            extract_cmd = (
                "rm -rf /tmp/smsly-hosting-src && "
                "mkdir -p /tmp/smsly-hosting-src && "
                "tar -xzf /tmp/smsly-hosting-src.tar.gz "
                "-C /tmp/smsly-hosting-src --strip-components=1"
            )
            stdin, stdout, stderr = ssh.exec_command(extract_cmd)
            extract_exit = stdout.channel.recv_exit_status()
            extract_err = stderr.read().decode("utf-8", errors="replace").strip()
            if extract_exit != 0:
                raise RuntimeError(
                    "Failed to prepare local source bundle on target: "
                    f"{extract_err or f'exit {extract_exit}'}"
                )
            run_prefix = "cd /tmp/smsly-hosting-src && "

        sftp.close()
        _append_log(server, "✅ Install script uploaded")

        # ── Step 3: Run install script ──
        _append_log(server, "⚙️ Running CloudNeuron installer (this may take 5-15 minutes)...")

        # Build non-interactive environment
        env_vars = (
            f"NON_INTERACTIVE=1 SKIP_SCREEN=1 USE_SSL=false DOMAIN={server.host}"
        )
        cmd = f"{run_prefix}{env_vars} bash /tmp/smsly-install.sh 2>&1"

        # Execute with a channel for streaming output
        transport = ssh.get_transport()
        channel = transport.open_session()
        channel.set_combine_stderr(True)
        channel.settimeout(PROVISION_TIMEOUT_SECONDS + 60)
        channel.exec_command(cmd)
        started_at = time.monotonic()

        # Stream output in chunks
        buffer = ""
        credentials_file_content = ""
        while True:
            if channel.recv_ready():
                chunk = channel.recv(4096).decode("utf-8", errors="replace")
                buffer += chunk

                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        _append_log(server, line)

                        # Look for credentials file path
                        if (
                            "credentials saved" in line.lower()
                            or ".credentials" in line
                        ):
                            _append_log(server, "🔑 Credentials detected — extracting...")

            if channel.exit_status_ready():
                # Drain remaining output
                while channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    buffer += chunk
                for line in buffer.strip().split("\n"):
                    if line.strip():
                        _append_log(server, line.strip())
                break

            elapsed = time.monotonic() - started_at
            if elapsed > PROVISION_TIMEOUT_SECONDS:
                try:
                    channel.close()
                except Exception:
                    pass
                raise TimeoutError(
                    f"Install script timed out after {PROVISION_TIMEOUT_SECONDS} seconds"
                )

            time.sleep(0.5)

        exit_code = channel.recv_exit_status()
        _append_log(server, f"\n📋 Install script exited with code: {exit_code}")

        if exit_code != 0:
            raise RuntimeError(f"Install script failed with exit code {exit_code}")

        # ── Step 4: Extract credentials ──
        _append_log(server, "🔑 Reading credentials from server...")

        stdin, stdout, stderr = ssh.exec_command(
            "cat /root/.credentials 2>/dev/null || "
            "cat /opt/smsly-hosting/.credentials 2>/dev/null || "
            "cat /root/.smsly-credentials 2>/dev/null || "
            "cat /opt/smsly-hosting/.smsly-credentials 2>/dev/null || "
            "echo 'CREDS_NOT_FOUND'"
        )
        credentials_file_content = stdout.read().decode("utf-8", errors="replace")

        api_token = ""
        admin_user = ""
        admin_password = ""

        if "CREDS_NOT_FOUND" in credentials_file_content:
            _append_log(server, "⚠️ Credentials file not found — trying API token from .env")
            # Fallback: extract from .env file
            stdin, stdout, stderr = ssh.exec_command(
                "grep -E '^(ADMIN_TOKEN|API_TOKEN|AUTH_TOKEN|DJANGO_SUPERUSER_PASSWORD)=' "
                "/opt/smsly-hosting/.env 2>/dev/null | head -1"
            )
            token_line = stdout.read().decode("utf-8").strip()
            if "=" in token_line:
                value = token_line.split("=", 1)[1].strip().strip("'\"")
                if token_line.startswith("DJANGO_SUPERUSER_PASSWORD="):
                    admin_user = "admin"
                    admin_password = value
                else:
                    api_token = value
        else:
            # Parse credentials file
            for line in credentials_file_content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.lower().startswith("username:"):
                    admin_user = line.split(":", 1)[1].strip()
                elif line.lower().startswith("password:"):
                    admin_password = line.split(":", 1)[1].strip()
                elif "token" in line.lower() and ":" in line:
                    api_token = line.split(":", 1)[1].strip().strip("'\"")
                elif line.startswith(("API_TOKEN=", "ADMIN_TOKEN=", "AUTH_TOKEN=")):
                    api_token = line.split("=", 1)[1].strip().strip("'\"")

        # ── Step 5: Determine API URL ──
        # Check if SSL was set up (look for Caddy with domain)
        stdin, stdout, stderr = ssh.exec_command(
            "grep -E '^(DOMAIN|USE_SSL)=' /opt/smsly-hosting/.env 2>/dev/null"
        )
        env_pairs = {}
        for line in stdout.read().decode("utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_pairs[key.strip()] = value.strip().strip("'\"")

        env_domain = (env_pairs.get("DOMAIN") or "").strip().rstrip(".")
        use_ssl = (env_pairs.get("USE_SSL") or "").strip().lower() in (
            "1", "true", "yes", "on"
        )
        is_ip_domain = False
        try:
            ipaddress.ip_address(env_domain)
            is_ip_domain = True
        except ValueError:
            is_ip_domain = False

        if env_domain and env_domain not in ("localhost", "127.0.0.1") and not is_ip_domain:
            scheme = "https" if use_ssl else "http"
            api_url = f"{scheme}://{env_domain}"
        else:
            api_url = f"http://{server.host}:8090"

        # If installer did not emit an API token, exchange admin credentials for one.
        if not api_token and admin_user and admin_password:
            login_url = f"{api_url.rstrip('/')}/api/v1/auth/login/"
            try:
                response = requests.post(
                    login_url,
                    json={"username": admin_user, "password": admin_password},
                    timeout=20,
                    verify=api_url.startswith("https://"),
                )
                if response.ok:
                    payload = response.json()
                    api_token = payload.get("key") or payload.get("token", "")
                else:
                    _append_log(
                        server,
                        f"Warning: Could not mint API token from credentials (HTTP {response.status_code})",
                    )
            except Exception as token_exc:
                _append_log(server, f"Warning: API token exchange failed: {token_exc}")

        _append_log(server, f"🌐 API URL: {api_url}")
        _append_log(server, f"🔑 Token: {'*' * 8}...{api_token[-4:] if len(api_token) > 4 else '****'}")

        # ── Step 6: Update server record ──
        server.api_url = api_url
        server.api_token = api_token or ""
        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.status = ManagedServer.Status.ONLINE
        server.save(update_fields=[
            "api_url", "api_token", "provision_status", "status",
            "updated_at",
        ])

        _append_log(server, "\n✅ CloudNeuron provisioning complete!")
        _append_log(server, f"🖥️ Server '{server.name}' is now online at {api_url}")

    except SoftTimeLimitExceeded as exc:
        logger.exception("Provisioning soft-timeout for server %s", server_id)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            f"\nProvisioning timed out before completion: {exc}",
        )
    except Exception as exc:
        logger.exception("Provisioning failed for server %s", server_id)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(server, f"\n❌ Provisioning failed: {exc}")
    finally:
        if local_bundle_path and os.path.exists(local_bundle_path):
            try:
                os.remove(local_bundle_path)
            except OSError:
                pass
        try:
            if ssh is not None:
                ssh.close()
        except Exception:
            pass


@shared_task
def cleanup_stale_server_provisioning():
    """
    Auto-heal stale provisioning rows left behind by interrupted workers.

    This prevents ManagedServer entries from staying in PROVISIONING forever.
    """
    stale_after_seconds = max(3600, PROVISION_TIMEOUT_SECONDS * 2)
    cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
    stale_servers = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
        updated_at__lt=cutoff,
    )

    cleaned = 0
    for server in stale_servers:
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            (
                "Provisioning was auto-marked as failed because no updates were "
                f"received for over {stale_after_seconds} seconds."
            ),
        )
        cleaned += 1

    if cleaned:
        logger.warning("Auto-cleaned %d stale provisioning records", cleaned)
    return cleaned
