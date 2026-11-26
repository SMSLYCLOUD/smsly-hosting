import contextlib
import hashlib
import hmac as hmac_mod
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
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
    _docker_login_all,
    _env_bool,
    _get_master_mesh_ip,
    _get_master_wg_pubkey,
    _load_install_script,
    _node_queue_name,
    _prepare_remote_install_lock,
    _schedule_remote_reboot,
    _verify_agent_db_connectivity,
    build_agent_lite_install_env,
    server_connection_mode,
    server_install_mode,
    stage_media_application_repos,
    stage_media_repo_for_node,
)
from ..provisioning_resources import _ProvisioningResources

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, soft_time_limit=TASK_TIME_LIMIT_PROVISION[0], time_limit=TASK_TIME_LIMIT_PROVISION[1], name="apps.deployments.services.provisioner.provision_server")
def provision_server(self, server_id: str, skip_reboot: bool = False):
    # max_retries=0 is deliberate (not an omission): this 35-minute task
    # is not idempotent, so broker-level retries must never re-run it —
    # failures mark FAILED and the operator retries explicitly via
    # retry-provision (which supports --resume). The default would imply
    # at-least-once semantics that do not hold here.
    try:
        with transaction.atomic():
            server = ManagedServer.objects.select_for_update().get(id=server_id)
            conflict = (
                ManagedServer.objects.select_for_update()
                .filter(
                    host=server.host,
                    provision_status__in=(
                        ManagedServer.ProvisionStatus.PENDING,
                        ManagedServer.ProvisionStatus.PROVISIONING,
                        ManagedServer.ProvisionStatus.UPDATING,
                    ),
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
            # Preserve prior attempt logs (append separator, keep tail) so
            # a retry does not destroy forensics for the failure it retries.
            prior_logs = server.provision_logs or ""
            kept_tail = prior_logs[-50000:] if len(prior_logs) > 50000 else prior_logs
            server.provision_logs = (
                (kept_tail + "\n--- Re-provision started ---\n") if kept_tail else ""
            )
            server.save(
                update_fields=["provision_status", "provision_logs", "updated_at"]
            )
    except ManagedServer.DoesNotExist:
        logger.error("Server %s not found", server_id)
        return

    _append_log(server, "🚀 Starting Grid provisioning...")
    _append_log(server, f"📡 Connecting to {server.ssh_user}@{server.host}:{server.ssh_port}")

    # B4 snapshot: rollback must only clear/drop credentials THIS run
    # created. A failed re-provision of a healthy node must never nuke
    # the live credential set (previously: unconditional blank + DROP).
    resources = _ProvisioningResources(server)
    from ..helpers.database import node_db_user_preexists
    _snap_gateway_secret = str(server.gateway_secret or "")
    _snap_node_db_password = str(getattr(server, "node_db_password", "") or "")
    _node_db_username = f"node_agent_{str(server.id).split('-')[0]}"
    _node_db_preexisted = node_db_user_preexists(_node_db_username)
    resources.snapshot_credentials(
        gateway_secret=_snap_gateway_secret,
        node_db_password=_snap_node_db_password,
    )
    if _node_db_preexisted:
        resources.mark_db_user_preexisting(_node_db_username)

    from ..helpers import (
        _get_ssh_client as _helpers_get_ssh_client,
        _harden_master_firewall,
        _harden_node_ssh,
        _clear_ssh_password_after_success,
        _clear_ssh_key_backup_after_success,
        _restrict_ssh_key_to_master_ip,
    )
    import apps.deployments.services.provisioner as _prov_pkg
    _get_ssh_client = getattr(_prov_pkg, "_get_ssh_client", _helpers_get_ssh_client)

    ssh = None
    local_bundle_path = None
    provision_start_time = time.monotonic()
    try:
        prefer_local_bundle = str(
            os.environ.get("SMSLY_PROVISION_USE_LOCAL_BUNDLE", "false")
        ).strip().lower() not in ("0", "false", "no", "off")
        use_local_bundle = prefer_local_bundle

        _harden_wg_ip = _harden_master_firewall(server)
        if _harden_wg_ip:
            resources.track_iptables_port5000(_harden_wg_ip)
        try:
            _validated_ip = str(ipaddress.ip_address(server.host))
            resources.track_iptables_port5000(_validated_ip)
        except (ValueError, TypeError):
            # Hostname nodes: no firewall rule can be written for an
            # unresolved name, and tracking the raw hostname produces
            # bogus ufw/iptables rules that fail silently on rollback.
            # Skip tracking (do NOT record the hostname); if the master
            # firewall defaults to deny, registry pulls will fail and
            # the installer log will show it.
            logger.warning(
                "Skipping firewall rule tracking for non-IP host %s",
                server.host,
            )
        if getattr(server, "is_lite_agent", False):
            try:
                _lite_ip = str(ipaddress.ip_address(server.host))
                for port in ("5432",):
                    resources.track_firewall_rule(_lite_ip, port)
            except (ValueError, TypeError):
                logger.warning(
                    "Skipping lite DB firewall tracking for non-IP host %s",
                    server.host,
                )

        ssh = _get_ssh_client(server)
        _append_log(server, "✅ SSH connection established")

        if _restrict_ssh_key_to_master_ip(ssh, server):
            resources.track_ssh_key_added()

        _harden_node_ssh(ssh, server)

        _append_log(server, "🚀 Uploading install script...")
        sftp = ssh.open_sftp()
        try:
            script_name = "install-media-node.sh" if server_install_mode(server) == "media" else "install.sh"
            install_script_content, install_script_source = _load_install_script(script_name=script_name)
            _append_log(server, f"🚀 Installer source: {install_script_source}")
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
        finally:
            with contextlib.suppress(Exception):
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

        master_ip = (os.environ.get("PUBLIC_IP") or "").strip() or "127.0.0.1"
        try:
            import ipaddress as _ip2
            if _ip2.ip_address(master_ip).is_loopback:
                try:
                    _remote_is_loopback = bool(_ip2.ip_address(str(server.host or "").strip()))
                except ValueError:
                    _remote_is_loopback = False
                if not _remote_is_loopback:
                    _append_log(
                        server,
                        "⚠️ PUBLIC_IP is unset/loopback — MASTER_IP falls back to 127.0.0.1; "
                        "registry pulls from this node may fail. Set PUBLIC_IP.",
                    )
        except ValueError:
            pass
        install_env = {
            "NON_INTERACTIVE": "1",
            "SKIP_REBOOT": "1",
            "SMSLY_STRICT_VERIFY": "1",
            "MASTER_IP": master_ip,
            "SMSLY_BRANCH": os.environ.get("SMSLY_BRANCH", "main"),
            "USE_SSL": "false",
            "SMSLY_NODE_HOST": server.host,
        }

        # Defaults mirror the dashboard DEFAULT_NODE_COMPONENTS
        # (frontend/src/app/servers/page.tsx): an absent key means
        # "default", never "everything off" — API-created servers without
        # an explicit components dict get the same baseline as UI ones.
        # An explicit false always wins (opt-out).
        node_components = {
            "observability": True,
            "security": True,
            "crowdsec": False,
            "falco": False,
            "spire": False,
            "log_shipping": True,
            **(getattr(server, "node_components", None) or {}),
        }
        install_env["NODE_OBSERVABILITY"] = "1" if node_components.get("observability") else "0"
        install_env["NODE_SECURITY"] = "1" if node_components.get("security") else "0"
        install_env["NODE_CROWDSEC"] = "1" if node_components.get("crowdsec") else "0"
        install_env["NODE_FALCO"] = "1" if node_components.get("falco") else "0"
        install_env["NODE_SPIRE"] = "1" if node_components.get("spire") else "0"
        install_env["NODE_LOG_SHIPPING"] = "1" if node_components.get("log_shipping") else "0"

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
            # No --mode flag needed for install-media-node.sh
            _append_log(server, "🚀 Media node: installing voice/video bare-metal stack")
            master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
            install_env["MASTER_IP"] = master_ip
            install_env["MASTER_MESH_IP"] = _get_master_mesh_ip()
            if not server.gateway_secret:
                server.gateway_secret = secrets.token_hex(32)
                server.save(update_fields=["gateway_secret", "updated_at"])
            install_env["NODE_ID"] = str(server.id)
            install_env["SERVER_ID"] = str(server.id)
            install_env["GATEWAY_SECRET"] = str(server.gateway_secret)
            master_url = os.environ.get("PUBLIC_URL", "https://grid.smsly.cloud").rstrip("/")
            install_env["MASTER_API_URL"] = f"{master_url}/api/v1"

            # Stage the media code repo onto the node first: the master
            # fetches it with a short-lived GitHub App token and copies
            # the files over SSH, so no GitHub credential ever lands on
            # the node. Returns a file:// URL (+ legacy token fallback).
            repo_url, repo_token = stage_media_repo_for_node(ssh, server)
            stage_media_application_repos(ssh, server)
            install_env["MEDIA_VOICE_SOURCE_DIR"] = "/opt/smsly-voice-src"
            install_env["MEDIA_VIDEO_SOURCE_DIR"] = "/opt/smsly-video-src"
            install_env["MEDIA_ATTESTATION_SOURCE_DIR"] = "/opt/smsly-attestation"
            if repo_url:
                install_env["MEDIA_REPO_URL"] = str(repo_url)
                install_args.extend(["--repo-url", str(repo_url)])
            if repo_token:
                # Env-only: install.sh reads MEDIA_REPO_TOKEN from the
                # environment (never a --repo-token flag — it would be
                # ignored AND leak the secret in `ps` argv).
                install_env["MEDIA_REPO_TOKEN"] = str(repo_token)
                _append_log(
                    server,
                    "⚠️ Legacy media repo token placed on node env "
                    "(profile fallback) — rotate MediaNodeProfile token after provision.",
                )
        elif install_mode == "node":
            install_args.append("--mode=node")
            install_env["COMPOSE_FILE"] = "infrastructure/docker/docker-compose.node.yml"
            if not server.gateway_secret:
                server.gateway_secret = secrets.token_hex(32)
                server.save(update_fields=["gateway_secret", "updated_at"])
            install_env["SERVER_ID"] = str(server.id)
            install_env["SMSLY_NODE_ID"] = str(server.id)
            install_env["GATEWAY_SECRET"] = str(server.gateway_secret)

            from apps.deployments.models.core import PlatformConfig
            from apps.domains.services.dns import ensure_dns_records
            config = PlatformConfig.load()
            cf_token = config.cloudflare_api_token
            root_domain = config.domain

            if root_domain:
                install_env["MASTER_URL"] = f"https://{root_domain}"

            if cf_token and root_domain:
                from apps.deployments.services.provisioner.helpers.server_config import (
                    allocate_node_number as _alloc,
                )
                # Single allocator under the mesh lock — concurrent SSH runs
                # for different servers cannot draw the same number.
                locked = (
                    ManagedServer.objects.filter(id=server.id).first()
                )
                if locked and locked.node_number:
                    server.node_number = locked.node_number
                elif not server.node_number:
                    server.node_number, _ = _alloc(exclude_server_id=server.id)

                node_number = server.node_number or 1
                parts = root_domain.split(".")
                if len(parts) > 2:
                    node_domain = f"grid{node_number}.{'.'.join(parts[1:])}"
                else:
                    node_domain = f"grid{node_number}.{root_domain}"

                server.node_domain = node_domain
                server.save(update_fields=["node_number", "node_domain", "updated_at"])

                _append_log(server, f"🌐 Automated TLS: Generating DNS record for node ({node_domain})...")
                try:
                    ensure_dns_records([node_domain], server.host, cf_token)
                    resources.track_dns_domain(node_domain)
                    _append_log(server, "✅ Automated TLS: DNS sync OK.")

                    install_env["CLOUDFLARE_API_TOKEN"] = cf_token
                    install_env["DOMAIN"] = node_domain
                    install_env["USE_SSL"] = "true"
                    install_env["ACME_EMAIL"] = f"admin@{root_domain}"
                except Exception as e:
                    logger.error("Failed to provision DNS/TLS for node %s: %s", server.name, e)
                    _append_log(server, f"⚠️ Automated TLS: Failed to generate DNS record: {e} — keeping HTTP; retry provisioning to enable TLS.")
                    server.node_domain = ""
                    server.save(update_fields=["node_number", "node_domain", "updated_at"])

            # Pre-provision the WG peer BEFORE the installer runs: the node
            # compose file hard-requires NODE_MESH_BIND_IP (coredns ports)
            # and the value must be this node's own mesh address. The token
            # flow does this at mint time; without it every fresh SSH node
            # install dies at stack deploy (compose: required variable
            # NODE_MESH_BIND_IP is missing).
            try:
                _wg_addr_now = str(getattr(server, "wg_address", "") or "").strip()
                _wg_peer_live = False
                if _wg_addr_now:
                    try:
                        from apps.deployments.models.mesh import WireGuardPeer as _WGP
                        _wg_peer_live = _WGP.objects.filter(
                            server=server, wg_address=_wg_addr_now,
                            is_local=False, is_active=True,
                        ).exists()
                        if not _wg_peer_live:
                            # Stale address from a rolled-back attempt (peer
                            # row deleted, field kept) — must not be trusted
                            # or the node binds a dead mesh IP.
                            server.wg_address = None
                            server.save(update_fields=["wg_address", "updated_at"])
                    except Exception:
                        _wg_peer_live = False
                if not _wg_peer_live:
                    from apps.deployments.services.wireguard_service import WireGuardService as _WGSvc
                    _mesh_result = _WGSvc.ensure_server_in_default_mesh(
                        server,
                        deploy_async=True,
                    )
                    _wg_peer_id = (_mesh_result or {}).get("peer")
                    if _wg_peer_id:
                        resources.track_wg_peer(_wg_peer_id)
                    if (_mesh_result or {}).get("wg_address"):
                        _append_log(
                            server,
                            f"VPN mesh pre-provisioned: {server.wg_address}",
                        )
            except Exception as _wg_exc:
                logger.warning(
                    "Node WG pre-provision failed for %s: %s", server.name, _wg_exc,
                )
                _append_log(
                    server,
                    f"⚠️ Mesh pre-provision failed ({_wg_exc}) — continuing "
                    "without mesh bind; coredns may fail to start.",
                )
            _wg_addr = str(getattr(server, "wg_address", "") or "").strip()
            _wg_privkey = ""
            _wg_port = int(getattr(server, "provider_metadata", {}).get("wg_port") or 51820)
            if _wg_addr:
                # The node's wg0 address (NODE_MESH_IP) AND the provisioned
                # private key must travel together: the installer converges
                # the node's keypair on WG_PRIVATE_KEY (a fresh local key
                # would fail the handshake — master only trusts the
                # provisioned one), and binds the interface to NODE_MESH_IP.
                # Without NODE_MESH_IP the node keeps the 10.100.0.2
                # placeholder while coredns binds the real address →
                # "cannot assign requested address" and the whole stack
                # fails behind it (every service gates on healthy deps).
                try:
                    from apps.deployments.models.mesh import WireGuardPeer as _WGP2
                    _wg_peer_row = _WGP2.objects.filter(
                        server=server, wg_address=_wg_addr,
                        is_local=False, is_active=True,
                    ).select_related("mesh").only("private_key", "mesh__listen_port").first()
                    _wg_privkey = str(getattr(_wg_peer_row, "private_key", "") or "").strip()
                    if _wg_peer_row and getattr(_wg_peer_row, "mesh", None):
                        _wg_port = int(
                            getattr(server, "provider_metadata", {}).get("wg_port")
                            or getattr(_wg_peer_row.mesh, "listen_port", 51820)
                            or 51820
                        )
                except Exception:
                    _wg_privkey = ""
                install_env["WG_ADDRESS"] = _wg_addr
                install_env["NODE_MESH_BIND_IP"] = _wg_addr
                install_env["NODE_MESH_IP"] = _wg_addr
                install_env["WG_PRIMARY_PORT"] = str(_wg_port)
                if _wg_privkey:
                    install_env["WG_PRIVATE_KEY"] = _wg_privkey
                else:
                    logger.warning(
                        "Node WG pre-provision for %s has no private key; "
                        "node will generate its own and mesh handshake may fail.",
                        server.name,
                    )
                _master_mesh_ip = _get_master_mesh_ip()
                if _master_mesh_ip:
                    install_env["MASTER_MESH_IP"] = _master_mesh_ip
                _master_wg_pub = _get_master_wg_pubkey()
                if _master_wg_pub:
                    install_env["MASTER_WG_PUBKEY"] = _master_wg_pub
                if master_ip and master_ip != "127.0.0.1":
                    install_env["MASTER_WG_ENDPOINT"] = f"{master_ip}:{_wg_port}"

        stdin, stdout, stderr = ssh.exec_command("test -f /opt/smsly-hosting/.smsly_install_state && echo 'RESUME' || echo 'FRESH'", timeout=30)
        remote_mode = stdout.read().decode().strip()
        if "RESUME" in remote_mode:
            _append_log(server, "ℹ️ Found partial installation state. Resuming from last checkpoint...")
            install_args.append("--resume")

        if use_local_bundle:
            install_env["SMSLY_FORCE_SOURCE_SYNC"] = "1"
            install_env["SMSLY_INSTALL_WORKDIR"] = "/tmp/smsly-hosting-src"

        install_args_str = " ".join(shlex.quote(arg) for arg in install_args)
        # Secrets travel via a root-only env file, never argv: env-prefix
        # assignments (KEY='value' bash ...) are visible to anyone on the
        # node via `ps` for the whole 5-15 minute installer run.
        remote_env_path = "/tmp/smsly-install.env"
        _env_file_lines = []
        for _env_key, _env_val in install_env.items():
            if _env_val is None:
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(_env_key)):
                continue
            _escaped = str(_env_val).replace("'", "'\"'\"'")
            _env_file_lines.append(f"{_env_key}='{_escaped}'")
        sftp_env = ssh.open_sftp()
        try:
            _remote_env = sftp_env.open(remote_env_path, "w")
            try:
                _remote_env.write("\n".join(_env_file_lines) + "\n")
                _remote_env.flush()
            finally:
                _remote_env.close()
            sftp_env.chmod(remote_env_path, 0o600)
        finally:
            sftp_env.close()
        cmd = (
            f"{run_prefix}set -a; . {remote_env_path}; set +a; "
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

        if exit_code != 0:
            server.provision_status = ManagedServer.ProvisionStatus.FAILED
            server.save(update_fields=["provision_status"])
            raise RuntimeError(f"Install script failed with exit code {exit_code}")

        try:
            from ..helpers.env import _installer_logs_confirm_success
            _tail = (server.provision_logs or "")[-20000:]
            if not _installer_logs_confirm_success(_tail):
                _append_log(
                    server,
                    "⚠️ Installer exited 0 but no INSTALLATION SUCCESSFUL marker found — "
                    "treating as suspect; verify node health before trusting.",
                )
        except Exception:
            pass

        with contextlib.suppress(Exception):
            ssh.exec_command(
                "shred -u /tmp/smsly-install.sh /tmp/smsly-install.env "
                "/tmp/smsly-hosting-src.tar.gz "
                "/tmp/smsly-hosting-src 2>/dev/null || "
                "rm -rf /tmp/smsly-install.sh /tmp/smsly-install.env "
                "/tmp/smsly-hosting-src.tar.gz /tmp/smsly-hosting-src"
            )

        if install_mode == "node":
            with contextlib.suppress(Exception):
                _, _wg_out, _ = ssh.exec_command(
                    "wg show wg0 listen-port 2>/dev/null || "
                    "grep -m1 '^ListenPort' /etc/wireguard/wg0.conf 2>/dev/null | cut -d= -f2",
                    timeout=15,
                )
                _node_wg_port_raw = _wg_out.read().decode("utf-8", errors="replace").strip()
                if _node_wg_port_raw.isdigit():
                    _node_wg_port = int(_node_wg_port_raw)
                    from apps.deployments.models.mesh import WireGuardPeer as _WGP3
                    _node_peer = _WGP3.objects.filter(
                        server=server, is_local=False, is_active=True,
                    ).select_related("mesh").first()
                    if _node_peer and _node_peer.mesh and _node_wg_port == getattr(_node_peer.mesh, "listen_port_fallback", None):
                        _meta = dict(getattr(server, "provider_metadata", None) or {})
                        _meta["wg_port"] = _node_wg_port
                        server.provider_metadata = _meta
                        server.save(update_fields=["provider_metadata", "updated_at"])
                        _ep = str(_node_peer.endpoint or "").strip()
                        if ":" in _ep:
                            _ep_host, _ = _ep.rsplit(":", 1)
                            _new_ep = f"{_ep_host}:{_node_wg_port}"
                            if _node_peer.endpoint != _new_ep:
                                _node_peer.endpoint = _new_ep
                                _node_peer.save(update_fields=["endpoint", "updated_at"])
                                with contextlib.suppress(Exception):
                                    from apps.deployments.services.wireguard_service import WireGuardService as _WGSvcSync
                                    _local = _WGP3.objects.filter(
                                        mesh=_node_peer.mesh, is_local=True, is_active=True,
                                    ).first()
                                    if _local is not None:
                                        _WGSvcSync.deploy_config(_local)
                                with contextlib.suppress(Exception):
                                    from apps.deployments.tasks.infra.tasks_mesh import deploy_mesh_task
                                    deploy_mesh_task.delay(str(_node_peer.mesh_id))
                        _append_log(server, f"ℹ️ WireGuard node using fallback port {_node_wg_port}/udp")

        # Registry logins: passwords travel over the encrypted SSH channel
        # (stdin), never interpolated into remote argv (`ps`-visible).
        _append_log(server, "🔑 Logging into configured registries on node...")
        try:
            _docker_login_all(ssh, server)
            _append_log(server, "✅ Registry login pass completed")
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
        wg_addr = (getattr(server, "wg_address", "") or "").strip()
        if wg_addr:
            # Mesh first: same priority as health probes (WG :8000 before
            # public). The provision-token flow pre-assigns wg_address, so
            # prefer it when present; harmless when the node has no mesh yet.
            candidate_urls.append(f"http://{wg_addr}:8000")
            candidate_urls.append(f"http://{wg_addr}:8090")
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
            from apps.deployments.services.tls_verify import (
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
            from apps.deployments.services.tls_verify import (
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
                    token_value = (
                        payload.get("key")
                        or payload.get("token", "")
                        or response.cookies.get("__Host-auth_token", "")
                        or response.cookies.get("auth_token", "")
                    )
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
        elif not api_token and install_mode == "media":
            _append_log(
                server,
                "Media node does not run a Django backend; "
                "Master manages the node via the smsly-media-mgmt agent heartbeat.",
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
        if getattr(server, "wg_address", None) and "wg_address" not in update_fields:
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

        agent_db_ok = _verify_agent_db_connectivity(ssh, server, start_time=provision_start_time)

        if install_mode == "node":
            with contextlib.suppress(Exception):
                from apps.deployments.models.core import PlatformConfig as _PC
                if not getattr(_PC.load(), "trivy_enabled", True):
                    _ti, _to, _te = ssh.exec_command(
                        "docker exec -i smsly-hosting-backend-1 python manage.py shell -c "
                        "\"from apps.deployments.models.core import PlatformConfig; "
                        "c = PlatformConfig.load(); c.trivy_enabled = False; "
                        "c.save(update_fields=['trivy_enabled'])\"",
                        timeout=30,
                    )
                    _to.channel.recv_exit_status()

        if getattr(server, "wg_address", None) and "wg_address" not in update_fields:
            update_fields.append("wg_address")
        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.status = (
            ManagedServer.Status.ONLINE if agent_db_ok
            else ManagedServer.Status.DEGRADED
        )
        try:
            _meta = dict(server.provider_metadata or {})
            if _meta.pop("node_db_user_pending", None) is not None:
                server.provider_metadata = _meta
                if "provider_metadata" not in update_fields:
                    update_fields.append("provider_metadata")
        except Exception:
            pass
        server.save(update_fields=update_fields)

        _append_log(server, "✅ Grid provisioning complete!")
        if agent_db_ok:
            _append_log(server, f"🖥️ Server '{server.name}' is now online at {api_url}")
        else:
            _append_log(
                server,
                f"⚠️ Server '{server.name}' provisioned but DEGRADED "
                f"(agent DB unhealthy) at {api_url} — check DB connectivity.",
            )

        if api_token and not api_token.startswith("smsly_"):
            _append_log(server, "🔄 Attempting auto token exchange for long-lived API token...")
            try:
                ssh_password = str(server.ssh_password or "").strip()
                if ssh_password:
                    from apps.deployments.services.tls_verify import (
                        _check_pin_after_handshake,
                        resolve_tls_verify_for_url,
                    )
                    for username in ("admin", "root"):
                        exchange_url = f"{api_url}/api/v1/auth/node-token-exchange/"
                        # Never send host SSH credentials over an
                        # unverified channel: pin/verify like the HMAC
                        # path above. On plain HTTP to a non-local target
                        # the exchange is skipped entirely.
                        verify, fingerprint = resolve_tls_verify_for_url(
                            exchange_url
                        )
                        if not exchange_url.startswith("https://"):
                            logger.warning(
                                "Skipping node token exchange for %s: non-TLS URL",
                                server.name,
                            )
                            _append_log(
                                server,
                                "⚠️ Token re-exchange requires HTTPS — skipping; "
                                "node keeps its short-lived login token. "
                                "Dashboard shows api_token type; switch the node "
                                "to TLS to get a long-lived smsly_ token.",
                            )
                            break
                        resp = requests.post(
                            exchange_url,
                            json={
                                "username": username,
                                "password": ssh_password,
                                "node_name": f"Primary-{server.owner.username}",
                            },
                            timeout=15,
                            verify=verify,
                            stream=True,
                        )
                        if fingerprint:
                            _check_pin_after_handshake(resp, fingerprint)
                        _ = resp.content
                        resp.close()
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
            import time as _obs_time
            _obs_start = _obs_time.monotonic()
            _obs_budget = 240.0
            def _obs_left() -> bool:
                return (_obs_time.monotonic() - _obs_start) < _obs_budget
            if _obs_left() and deploy_docker_labels_exporter_on_node(server):
                _append_log(server, "✓ docker-labels exporter deployed")
            if _obs_left() and deploy_promtail_on_node(server):
                _append_log(server, "✓ Promtail deployed")
            if _obs_left() and deploy_cadvisor_on_node(server):
                _append_log(server, "✓ cAdvisor deployed")
            else:
                _append_log(server, "⚠ cAdvisor deployment failed (non-critical)")
            if _obs_left() and deploy_node_exporter_on_node(server):
                _append_log(server, "✓ Node Exporter deployed")
            else:
                _append_log(server, "⚠ Node Exporter deployment failed (non-critical)")
            if not _obs_left():
                _append_log(server, "⚠ observability budget exceeded — remaining agents skipped")
            write_docker_labels_targets()
        except Exception as exc:
            _append_log(server, f"⚠ observability deployment skipped: {exc}")

        # Clear SSH password now that provisioning succeeded (key-only auth)
        _clear_ssh_password_after_success(server)
        # Drop the stashed operator key backup — it exists for FAILURE
        # rollback only; on success the record holds the generated key.
        _clear_ssh_key_backup_after_success(server)

        if not skip_reboot and _env_bool("SMSLY_PROVISION_REBOOT_ON_SUCCESS", default=True):
            _append_log(server, "Scheduling remote reboot after successful provisioning.")
            if _schedule_remote_reboot(ssh, server, "provisioning"):
                server.status = (
                    ManagedServer.Status.DEGRADED
                    if not agent_db_ok else ManagedServer.Status.UNKNOWN
                )
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
        server.status = ManagedServer.Status.UNKNOWN
        server.save(update_fields=["provision_status", "status", "updated_at"])
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
        server.status = ManagedServer.Status.UNKNOWN
        server.save(update_fields=["provision_status", "status", "updated_at"])
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
                # The secrets env file must not survive EITHER path: on
                # failure the installer never reaches its own shred step.
                with contextlib.suppress(Exception):
                    ssh.exec_command(
                        "shred -u /tmp/smsly-install.env 2>/dev/null || "
                        "rm -f /tmp/smsly-install.env",
                        timeout=15,
                    )
                ssh.close()
        except Exception as exc:
            logger.debug("Failed to close SSH connection during cleanup: %s", exc)
