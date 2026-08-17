import contextlib
import hashlib
import hmac as hmac_mod
import ipaddress
import json
import logging
import os
import secrets
import shlex
import subprocess
import time

import requests
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction

from apps.deployments.constants import TASK_TIME_LIMIT_PROVISION
from apps.deployments.models.servers import ManagedServer

from .docker_mirror import _ensure_docker_mirror, _stop_docker_mirror
from ..helpers import (
    PROVISION_TIMEOUT_SECONDS,
    _append_log,
    _build_local_source_bundle,
    _env_bool,
    _get_master_mesh_ip,
    _installer_logs_confirm_success,
    _load_install_script,
    _node_queue_name,
    _prepare_remote_install_lock,
    _registry_login_commands,
    _schedule_remote_reboot,
    _shell_env_assignments,
    _verify_agent_db_connectivity,
    build_agent_lite_install_env,
    server_connection_mode,
    server_install_mode,
)
from ..provisioning_resources import _ProvisioningResources

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, soft_time_limit=TASK_TIME_LIMIT_PROVISION[0], time_limit=TASK_TIME_LIMIT_PROVISION[1], name="apps.deployments.services.provisioner.provision_server")
def provision_server(self, server_id: str, skip_reboot: bool = False):
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
            server.provision_status = ManagedServer.ProvisionStatus.PROVISIONING
            server.provision_logs = ""
            server.save(
                update_fields=["provision_status", "provision_logs", "updated_at"]
            )
    except ManagedServer.DoesNotExist:
        logger.error("Server %s not found", server_id)
        return

    _append_log(server, "🚀 Starting Grid provisioning...")
    _append_log(server, f"📡 Connecting to {server.ssh_user}@{server.host}:{server.ssh_port}")

    from ..helpers import (
        _get_ssh_client,
        _harden_master_firewall,
        _harden_node_ssh,
        _clear_ssh_password_after_success,
        _restrict_ssh_key_to_master_ip,
    )

    ssh = None
    local_bundle_path = None
    resources = _ProvisioningResources(server)
    provision_start_time = time.monotonic()
    try:
        prefer_local_bundle = str(
            os.environ.get("SMSLY_PROVISION_USE_LOCAL_BUNDLE", "false")
        ).strip().lower() not in ("0", "false", "no", "off")
        use_local_bundle = prefer_local_bundle

        _harden_master_firewall(server)
        try:
            _validated_ip = str(ipaddress.ip_address(server.host))
        except (ValueError, TypeError):
            _validated_ip = server.host
        resources.track_iptables_port5000(_validated_ip)
        if getattr(server, "is_lite_agent", False):
            for port in ("5432",):
                resources.track_firewall_rule(server.host, port)

        ssh = _get_ssh_client(server)
        _append_log(server, "✅ SSH connection established")

        _restrict_ssh_key_to_master_ip(ssh, server)
        if server.ssh_key:
            resources.track_ssh_key_added()

        _harden_node_ssh(ssh, server)

        _append_log(server, "📦 Uploading install script...")
        sftp = ssh.open_sftp()

        install_script_content, install_script_source = _load_install_script()
        _append_log(server, f"📥 Installer source: {install_script_source}")
        if use_local_bundle:
            _append_log(
                server,
                "ℹ️ Provisioning in local-bundle mode (no GitHub clone required).",
            )
        else:
            _append_log(
                server,
                "ℹ️ Installer repository is public; using unauthenticated GitHub clone.",
            )
        remote_script = sftp.open("/tmp/smsly-install.sh", "w")
        try:
            remote_script.write(install_script_content)
            remote_script.flush()
        finally:
            remote_script.close()
        sftp.chmod("/tmp/smsly-install.sh", 0o755)

        sftp.close()
        _append_log(server, "✅ Install script uploaded")

        run_prefix = ""
        if use_local_bundle:
            _append_log(server, "📦 Uploading local source bundle for provisioning fallback...")
            local_bundle_path = _build_local_source_bundle()
            try:
                bundle_size = os.path.getsize(local_bundle_path)
                _append_log(server, f"ℹ️ Local bundle size: {bundle_size / 1024 / 1024:.2f} MB")

                sftp_bundle = ssh.open_sftp()
                try:
                    sftp_bundle.put(local_bundle_path, "/tmp/smsly-hosting-src.tar.gz")
                    remote_size = sftp_bundle.stat("/tmp/smsly-hosting-src.tar.gz").st_size
                finally:
                    sftp_bundle.close()
                if remote_size != bundle_size:
                    raise RuntimeError(
                        "Uploaded source bundle size mismatch: "
                        f"local={bundle_size} remote={remote_size}"
                    )

                _append_log(server, "📦 Extracting source bundle on target...")
                extract_cmd = (
                    "rm -rf /tmp/smsly-hosting-src && "
                    "mkdir -p /tmp/smsly-hosting-src && "
                    "tar -xzf /tmp/smsly-hosting-src.tar.gz "
                    "-C /tmp/smsly-hosting-src && "
                    "test -f /tmp/smsly-hosting-src/docker-compose.prod.yml"
                )
                stdin, stdout, stderr = ssh.exec_command(extract_cmd, timeout=120)
                extract_exit = stdout.channel.recv_exit_status()
                extract_err = stderr.read().decode("utf-8", errors="replace").strip()
                if extract_exit != 0:
                    raise RuntimeError(
                        "Failed to prepare local source bundle on target: "
                        f"{extract_err or f'exit {extract_exit}'}"
                    )
                run_prefix = "cd /tmp/smsly-hosting-src && "
            finally:
                if os.path.exists(local_bundle_path):
                    with contextlib.suppress(OSError):
                        os.remove(local_bundle_path)

        _prepare_remote_install_lock(ssh, server)
        _ensure_docker_mirror()
        _append_log(server, "⚙️ Running Grid installer (this may take 5-15 minutes)...")

        master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
        install_env = {
            "NON_INTERACTIVE": "1",
            "SKIP_REBOOT": "1",
            "SMSLY_STRICT_VERIFY": "1",
            "MASTER_IP": master_ip,
            "SMSLY_BRANCH": os.environ.get("SMSLY_BRANCH", "main"),
            "USE_SSL": "false",
            "SMSLY_NODE_HOST": server.host,
        }

        node_components = getattr(server, "node_components", None) or {}
        install_env["NODE_OBSERVABILITY"] = "1" if node_components.get("observability") else "0"
        install_env["NODE_SECURITY"] = "1" if node_components.get("security") else "0"
        install_env["NODE_CROWDSEC"] = "1" if node_components.get("crowdsec") else "0"
        install_env["NODE_FALCO"] = "1" if node_components.get("falco") else "0"

        install_args: list[str] = []
        install_mode = server_install_mode(server)
        if install_mode == "agent-lite":
            lite_env, lite_messages = build_agent_lite_install_env(
                server,
                master_ip=master_ip,
            )
            for message in lite_messages:
                _append_log(server, message)
            install_env.update(lite_env)
            install_args.append("--mode=agent-lite")
            node_db_user = (server.provider_metadata or {}).get("node_db_user")
            if node_db_user:
                resources.track_db_user(node_db_user)
        elif install_mode == "media":
            install_args.append("--mode=media-node")
            _append_log(server, "📡 Media node: installing voice/video bare-metal stack")
            master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
            install_env["MASTER_IP"] = master_ip
            install_env["MASTER_MESH_IP"] = _get_master_mesh_ip()
        elif install_mode == "node":
            install_args.append("--mode=node")

            from apps.deployments.models.core import PlatformConfig
            from apps.domains.services.dns import ensure_dns_records
            config = PlatformConfig.load()
            cf_token = config.cloudflare_api_token
            root_domain = config.domain

            if cf_token and root_domain:
                node_slug = str(server.id).split('-')[0]
                node_domain = f"node-{node_slug}.{root_domain}"

                _append_log(server, f"🌐 Automated TLS: Generating DNS record for node ({node_domain})...")
                try:
                    ensure_dns_records([node_domain], server.host, cf_token)
                    resources.track_dns_domain(node_domain)
                    _append_log(server, "✅ Automated TLS: DNS sync OK.")

                    install_env["CLOUDFLARE_API_TOKEN"] = cf_token
                    install_env["DOMAIN"] = node_domain
                    install_env["USE_SSL"] = "true"
                except Exception as e:
                    logger.error("Failed to provision DNS/TLS for node %s: %s", server.name, e)
                    _append_log(server, f"⚠️ Automated TLS: Failed to generate DNS record: {e}")

        stdin, stdout, stderr = ssh.exec_command("test -f /opt/smsly-hosting/.smsly_install_state && echo 'RESUME' || echo 'FRESH'")
        remote_mode = stdout.read().decode().strip()
        if "RESUME" in remote_mode:
            _append_log(server, "ℹ️ Found partial installation state. Resuming from last checkpoint...")
            install_args.append("--resume")

        if use_local_bundle:
            install_env["SMSLY_FORCE_SOURCE_SYNC"] = "1"
            install_env["SMSLY_INSTALL_WORKDIR"] = "/tmp/smsly-hosting-src"

        install_args_str = " ".join(shlex.quote(arg) for arg in install_args)
        cmd = (
            f"{run_prefix}{_shell_env_assignments(install_env)} "
            f"bash /tmp/smsly-install.sh {install_args_str} 2>&1"
        )

        transport = ssh.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport is not active; cannot open session")
        channel = transport.open_session()
        channel.set_combine_stderr(True)
        channel.settimeout(PROVISION_TIMEOUT_SECONDS + 60)
        channel.exec_command(cmd)
        started_at = time.monotonic()

        buffer = ""
        credentials_file_content = ""
        last_heartbeat = time.monotonic()
        while True:
            if channel.recv_ready():
                chunk = channel.recv(4096).decode("utf-8", errors="replace")
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        _append_log(server, line)

                        if (
                            "credentials saved" in line.lower()
                            or ".credentials" in line
                        ):
                            _append_log(server, "[cred] Credentials detected — extracting...")

            if channel.exit_status_ready():
                while channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    buffer += chunk
                for line in buffer.strip().split("\n"):
                    if line.strip():
                        _append_log(server, line.strip())
                break

            elapsed = time.monotonic() - started_at
            if elapsed > PROVISION_TIMEOUT_SECONDS:
                with contextlib.suppress(Exception):
                    channel.close()
                raise TimeoutError(
                    f"Install script timed out after {PROVISION_TIMEOUT_SECONDS} seconds"
                )

            # Heartbeat: update updated_at every 60s so the stale cleanup
            # task doesn't kill a still-running provision.
            if time.monotonic() - last_heartbeat >= 60:
                try:
                    server.save(update_fields=["updated_at"])
                except Exception:
                    pass
                last_heartbeat = time.monotonic()

            time.sleep(0.5)

        exit_code = channel.recv_exit_status()
        _append_log(server, f"\n[installer] Install script exited with code: {exit_code}")

        is_success_in_logs = _installer_logs_confirm_success(server.provision_logs)

        if exit_code != 0:
            if is_success_in_logs:
                _append_log(server, "Installer logs confirm success despite a non-zero SSH exit status.")
                server.provision_status = ManagedServer.ProvisionStatus.DONE
                server.save(update_fields=["provision_status"])
            else:
                server.provision_status = ManagedServer.ProvisionStatus.FAILED
                server.save(update_fields=["provision_status"])
                raise RuntimeError(f"Install script failed with exit code {exit_code}")

        with contextlib.suppress(Exception):
            ssh.exec_command(
                "shred -u /tmp/smsly-install.sh /tmp/smsly-hosting-src.tar.gz "
                "/tmp/smsly-hosting-src 2>/dev/null || "
                "rm -rf /tmp/smsly-install.sh /tmp/smsly-hosting-src.tar.gz /tmp/smsly-hosting-src"
            )

        registry_cmds = _registry_login_commands(server)
        if registry_cmds and registry_cmds != "true":
            _append_log(server, "🔑 Logging into configured registries on node...")
            try:
                stdin, stdout, stderr = ssh.exec_command(registry_cmds, timeout=60)
                reg_exit = stdout.channel.recv_exit_status()
                if reg_exit == 0:
                    _append_log(server, "✅ Docker login succeeded for all configured registries")
                else:
                    reg_err = stderr.read().decode("utf-8", errors="replace").strip()[:500]
                    _append_log(server, f"⚠️ Registry docker-login had non-zero exit ({reg_exit}): {reg_err}")
            except Exception as exc:
                _append_log(server, f"⚠️ Registry docker-login command failed: {exc}")

        _append_log(server, "[cred] Reading credentials from server...")

        stdin, stdout, stderr = ssh.exec_command(
            "cat /root/.credentials 2>/dev/null || "
            "cat /opt/smsly-hosting/.credentials 2>/dev/null || "
            "cat /root/.smsly-credentials 2>/dev/null || "
            "cat /opt/smsly-hosting/.smsly-credentials 2>/dev/null || "
            "echo 'CREDS_NOT_FOUND'",
            timeout=30,
        )
        credentials_file_content = stdout.read().decode("utf-8", errors="replace")

        api_token = ""
        admin_user = ""
        admin_password = ""

        def _parse_credentials(text: str) -> dict:
            """Parse credentials from key=value or 'key: value' formats."""
            result = {}
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip().lower()
                    value = value.strip().strip("'\"")
                    if key in ("admin_token", "api_token", "auth_token", "token"):
                        result["api_token"] = value
                    elif key == "django_superuser_password":
                        result["admin_user"] = "admin"
                        result["admin_password"] = value
                    elif key == "username":
                        result["admin_user"] = value
                    elif key == "password":
                        result["admin_password"] = value
                elif ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip().lower()
                    value = value.strip().strip("'\"")
                    if key == "username":
                        result["admin_user"] = value
                    elif key == "password":
                        result["admin_password"] = value
                    elif key in ("token", "api_token", "admin_token", "auth_token"):
                        result["api_token"] = value
            return result

        if "CREDS_NOT_FOUND" in credentials_file_content:
            _append_log(server, "⚠️ Credentials file not found — trying API token from .env")
            stdin, stdout, stderr = ssh.exec_command(
                "grep -E '^(ADMIN_TOKEN|API_TOKEN|AUTH_TOKEN|DJANGO_SUPERUSER_PASSWORD)=' "
                "/opt/smsly-hosting/.env 2>/dev/null",
                timeout=30,
            )
            env_text = stdout.read().decode("utf-8")
            parsed = _parse_credentials(env_text)
            api_token = parsed.get("api_token", "")
            admin_user = parsed.get("admin_user", "")
            admin_password = parsed.get("admin_password", "")
        else:
            parsed = _parse_credentials(credentials_file_content)
            api_token = parsed.get("api_token", "")
            admin_user = parsed.get("admin_user", "")
            admin_password = parsed.get("admin_password", "")

        if install_mode in ("agent-lite", "node"):
            _append_log(server, "[cred] Fetching node TLS certificate automatically...")
            stdin, stdout, stderr = ssh.exec_command("cat /opt/smsly-hosting/certs/registry.crt 2>/dev/null || cat /opt/smsly-hosting/caddy-config/certs/ip.crt 2>/dev/null", timeout=30)
            tls_cert = stdout.read().decode("utf-8", errors="replace").strip()
            if tls_cert and "-----BEGIN CERTIFICATE-----" in tls_cert:
                server.tls_cert_sha256 = hashlib.sha256(tls_cert.strip().encode('utf-8')).hexdigest()
                server.save(update_fields=["tls_cert_sha256"])
                _append_log(server, "✅ Node TLS certificate automatically fetched and saved!")
            else:
                _append_log(server, "⚠️ Warning: Could not automatically fetch node TLS certificate.")

        stdin, stdout, stderr = ssh.exec_command(
            "grep -E '^(DOMAIN|USE_SSL)=' /opt/smsly-hosting/.env 2>/dev/null",
            timeout=30,
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

        candidate_urls: list[str] = []
        if env_domain and env_domain not in ("localhost", "127.0.0.1") and not is_ip_domain:
            scheme = "https" if use_ssl else "http"
            candidate_urls.append(f"{scheme}://{env_domain}")
        candidate_urls.append(f"http://{server.host}")
        candidate_urls.append(f"http://{server.host}:8090")

        seen_urls: set[str] = set()
        api_urls = []
        for url in candidate_urls:
            normalized = url.rstrip("/")
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            api_urls.append(normalized)

        api_url = api_urls[0] if api_urls else f"http://{server.host}"

        remote_gateway_secret = ""
        try:
            _stdin, stdout, stderr = ssh.exec_command(
                "grep -E '^GATEWAY_SECRET=' /opt/smsly-hosting/.env "
                "2>/dev/null | head -1",
                timeout=30,
            )
            secret_line = stdout.read().decode("utf-8", errors="replace").strip()
            if "=" in secret_line:
                remote_gateway_secret = secret_line.split("=", 1)[1].strip().strip("'\"")
        except Exception as secret_exc:
            _append_log(server, f"Warning: could not read remote gateway secret: {secret_exc}")

        if not api_token and remote_gateway_secret:
            from .tls_verify import (
                _check_pin_after_handshake,
                resolve_tls_verify_for_url,
            )
            token_errors = []
            for candidate_url in api_urls:
                path = "/api/v1/auth/node-token-exchange-hmac/"
                body = json.dumps(
                    {"node_name": f"Node-{server.host or server.name}"[:100]},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                timestamp_val = str(int(time.time()))
                nonce = secrets.token_urlsafe(16)
                body_hash = hashlib.sha256(body).hexdigest()
                payload = f"POST|{path}|{timestamp_val}|{nonce}|{body_hash}"
                signature = hmac_mod.new(
                    remote_gateway_secret.encode(),
                    payload.encode(),
                    hashlib.sha256,
                ).hexdigest()
                verify, fingerprint = resolve_tls_verify_for_url(
                    candidate_url
                )
                try:
                    response = requests.post(
                        f"{candidate_url}{path}",
                        data=body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "X-Gateway-Signature-V2": signature,
                            "X-Request-Timestamp": timestamp_val,
                            "X-Request-Nonce": nonce,
                        },
                        timeout=20,
                        verify=verify,
                        stream=True,
                    )
                    if fingerprint:
                        _check_pin_after_handshake(response, fingerprint)
                    _ = response.content
                    response.close()
                    if not response.ok:
                        token_errors.append(f"{candidate_url}:HTTP {response.status_code}")
                        continue
                    token_value = response.json().get("token", "")
                    if token_value:
                        api_token = token_value
                        api_url = candidate_url
                        _append_log(server, "HMAC token exchange succeeded.")
                        break
                    token_errors.append(f"{candidate_url}:empty token payload")
                except Exception as token_exc:
                    token_errors.append(f"{candidate_url}:{token_exc}")
            if not api_token and token_errors:
                _append_log(
                    server,
                    "Warning: HMAC token exchange failed via all candidates: "
                    + "; ".join(token_errors),
                )

        if not api_token and admin_user and admin_password:
            from .tls_verify import (
                _check_pin_after_handshake,
                resolve_tls_verify_for_url,
            )
            token_errors = []
            for candidate_url in api_urls:
                login_url = f"{candidate_url}/api/v1/auth/login/"
                verify, fingerprint = resolve_tls_verify_for_url(
                    candidate_url
                )
                try:
                    response = requests.post(
                        login_url,
                        json={"username": admin_user, "password": admin_password},
                        timeout=20,
                        verify=verify,
                        stream=True,
                    )
                    if fingerprint:
                        _check_pin_after_handshake(response, fingerprint)
                    _ = response.content
                    response.close()
                    if not response.ok:
                        token_errors.append(f"{candidate_url}:HTTP {response.status_code}")
                        continue
                    payload = response.json()
                    token_value = payload.get("key") or payload.get("token", "")
                    if token_value:
                        api_token = token_value
                        api_url = candidate_url
                        break
                    token_errors.append(f"{candidate_url}:empty token payload")
                except Exception as token_exc:
                    token_errors.append(f"{candidate_url}:{token_exc}")

            if not api_token and token_errors:
                _append_log(
                    server,
                    "Warning: API token exchange failed via all candidates: "
                    + "; ".join(token_errors),
                )

        if not api_token and getattr(server, "is_lite_agent", False):
            _append_log(
                server,
                "Lite Agent install does not create a local admin token; "
                "Master will manage the node through the shared agent channel.",
            )
        elif not api_token:
            raise RuntimeError(
                "Provisioning completed but no API token was discovered. "
                "Verify gateway health and credentials, then retry provisioning."
            )

        _append_log(server, f"🌐 API URL: {api_url}")
        if api_token:
            _append_log(server, f"[cred] Token: {'*' * 8}...{api_token[-4:] if len(api_token) > 4 else '****'}")

        server.api_url = api_url
        server.api_token = api_token or ""
        provider_metadata = dict(server.provider_metadata or {})
        provider_metadata["connection_mode"] = server_connection_mode(server)
        update_fields = [
            "api_url", "api_token", "provision_status", "status",
            "provider_metadata", "updated_at",
        ]
        if getattr(server, "wg_address", None):
            update_fields.append("wg_address")
        if remote_gateway_secret:
            server.gateway_secret = remote_gateway_secret
            update_fields.append("gateway_secret")
            _append_log(server, "Remote HMAC gateway secret synchronized.")
        if getattr(server, "is_lite_agent", False):
            gateway_secret = str(install_env.get("MASTER_GATEWAY_SECRET") or "").strip()
            node_queue = str(install_env.get("SMSLY_NODE_QUEUE") or _node_queue_name(server))
            provider_metadata["node_id"] = str(server.id)
            provider_metadata["node_queue"] = node_queue
            provider_metadata["node_host"] = str(server.host or "")
            if gateway_secret and not remote_gateway_secret:
                server.gateway_secret = gateway_secret
                update_fields.append("gateway_secret")
                _append_log(
                    server,
                    "Lite Agent HMAC secret synchronized with the master.",
                )
            _append_log(server, f"Lite Agent node queue: {node_queue}")
        server.provider_metadata = provider_metadata

        wg_assigned = False
        try:
            from apps.deployments.services.wireguard_service import WireGuardService
            mesh_result = WireGuardService.ensure_server_in_default_mesh(
                server,
                deploy_async=True,
            )
            wg_assigned = bool(mesh_result.get("wg_address"))
            wg_peer_id = mesh_result.get("peer")
            if wg_peer_id:
                resources.track_wg_peer(wg_peer_id)
            _append_log(
                server,
                f"VPN mesh auto-connect queued: {mesh_result.get('wg_address')}",
            )
        except Exception as mesh_exc:
            _append_log(
                server,
                f"Warning: VPN mesh auto-connect could not complete yet: {mesh_exc}",
            )

        wg_ip = getattr(server, "wg_address", None) or ""
        if wg_ip:
            import contextlib as _ctx
            import subprocess as _sp
            try:
                import ipaddress as _ipa
                validated_wg = str(_ipa.ip_address(wg_ip))
                check = _sp.run(
                    ["iptables", "-C", "DOCKER-USER",
                     "-s", validated_wg, "-p", "tcp", "--dport", "5000",
                     "-j", "ACCEPT"],
                    capture_output=True, timeout=5,
                )
                if check.returncode != 0:
                    _sp.run(
                        ["iptables", "-I", "DOCKER-USER",
                         "-s", validated_wg, "-p", "tcp", "--dport", "5000",
                         "-j", "ACCEPT"],
                        capture_output=True, timeout=5,
                    )
                    resources.track_iptables_port5000(validated_wg)
                    _append_log(server, f"✅ iptables: Allowed mesh IP {validated_wg} -> registry port 5000")
            except Exception as exc:
                logger.debug("Failed to add WG IP iptables rule: %s", exc)

        if not wg_assigned and not getattr(server, "wg_address", None):
            try:
                from apps.deployments.models.mesh import WireGuardPeer
                fallback_peer = WireGuardPeer.objects.filter(
                    server=server,
                    is_local=False,
                    is_active=True,
                ).order_by("-created_at").first()
                if fallback_peer and fallback_peer.wg_address:
                    server.wg_address = fallback_peer.wg_address
                    server.save(update_fields=["wg_address", "updated_at"])
                    wg_assigned = True
                    _append_log(
                        server,
                        f"VPN mesh wg_address recovered from peer record: {fallback_peer.wg_address}",
                    )
            except Exception as exc:
                logger.debug("WireGuard address recovery failed: %s", exc)

        _verify_agent_db_connectivity(ssh, server, start_time=provision_start_time)

        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.status = ManagedServer.Status.ONLINE
        server.save(update_fields=update_fields)

        _append_log(server, "✅ Grid provisioning complete!")
        _append_log(server, f"🖥️ Server '{server.name}' is now online at {api_url}")

        if api_token and not api_token.startswith("smsly_"):
            _append_log(server, "🔄 Attempting auto token exchange for long-lived API token...")
            try:
                ssh_password = str(server.ssh_password or "").strip()
                if ssh_password:
                    for username in ("admin", "root"):
                        exchange_url = f"{api_url}/api/v1/auth/node-token-exchange/"
                        resp = requests.post(
                            exchange_url,
                            json={
                                "username": username,
                                "password": ssh_password,
                                "node_name": f"Primary-{server.owner.username}",
                            },
                            timeout=15,
                        )
                        if resp.status_code == 200:
                            new_token = resp.json().get("token")
                            if new_token:
                                server.api_token = new_token
                                server.save(update_fields=["api_token", "updated_at"])
                                _append_log(server, f"✅ Auto-exchanged for smsly_ API token: {'*' * 8}...{new_token[-4:] if len(new_token) > 4 else '****'}")
                                break
            except Exception as exc:
                _append_log(server, f"⚠️ Auto token exchange failed (non-critical): {exc}")

        try:
            from apps.autoscaler.services.prometheus_targets import (
                deploy_cadvisor_on_node,
                deploy_docker_labels_exporter_on_node,
                deploy_node_exporter_on_node,
                deploy_promtail_on_node,
                write_docker_labels_targets,
            )
            _append_log(server, "Deploying observability agents...")
            if deploy_docker_labels_exporter_on_node(server):
                _append_log(server, "✓ docker-labels exporter deployed")
            if deploy_promtail_on_node(server):
                _append_log(server, "✓ Promtail deployed")
            if deploy_cadvisor_on_node(server):
                _append_log(server, "✓ cAdvisor deployed")
            else:
                _append_log(server, "⚠ cAdvisor deployment failed (non-critical)")
            if deploy_node_exporter_on_node(server):
                _append_log(server, "✓ Node Exporter deployed")
            else:
                _append_log(server, "⚠ Node Exporter deployment failed (non-critical)")
            write_docker_labels_targets()
        except Exception as exc:
            _append_log(server, f"⚠ observability deployment skipped: {exc}")

        # Clear SSH password now that provisioning succeeded (key-only auth)
        _clear_ssh_password_after_success(server)

        if not skip_reboot and _env_bool("SMSLY_PROVISION_REBOOT_ON_SUCCESS", default=True):
            _append_log(server, "Scheduling remote reboot after successful provisioning.")
            if _schedule_remote_reboot(ssh, server, "provisioning"):
                server.status = ManagedServer.Status.UNKNOWN
                server.save(update_fields=["status", "updated_at"])
                _append_log(
                    server,
                    "Remote reboot scheduled. Health check will mark the node online after it returns.",
                )

    except SoftTimeLimitExceeded as exc:
        logger.exception("Provisioning soft-timeout for server %s", server_id)
        try:
            resources.rollback()
        except Exception as rollback_exc:
            logger.warning("Rollback raised during failure handling: %s", rollback_exc)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            f"\nProvisioning timed out before completion: {exc}",
        )
    except Exception as exc:
        logger.exception("Provisioning failed for server %s", server_id)
        try:
            resources.rollback()
        except Exception as rollback_exc:
            logger.warning("Rollback raised during failure handling: %s", rollback_exc)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(server, f"\n❌ Provisioning failed: {exc}")
        if server.ssh_key and not server.ssh_password:
            _append_log(
                server,
                "💡 If this was an SSH authentication failure with a generated or "
                "pasted key: make sure the matching public key is installed on the "
                "host (e.g. in ~/.ssh/authorized_keys or the provider's SSH key "
                "console) before retrying.",
            )
    finally:
        _stop_docker_mirror()
        if local_bundle_path and os.path.exists(local_bundle_path):
            with contextlib.suppress(OSError):
                os.remove(local_bundle_path)
        try:
            if ssh is not None:
                ssh.close()
        except Exception as exc:
            logger.debug("Failed to close SSH connection during cleanup: %s", exc)
