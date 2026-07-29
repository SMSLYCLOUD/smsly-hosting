import logging
import os
import shlex

logger = logging.getLogger(__name__)

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_HEAVY
from apps.deployments.models import (
    PlatformConfig,
)

from ..deploy.helpers import _env_bool
from ..remote.update import (
    _append_remote_update_log,
    _remote_update_postflight_script,
    _remote_update_preflight_script,
    _run_ssh_command,
)


@shared_task(name="apps.deployments.tasks.update_remote_server_task", soft_time_limit=TASK_TIME_LIMIT_HEAVY[0], time_limit=TASK_TIME_LIMIT_HEAVY[1])
def update_remote_server_task(server_id: str):
    """
    SSH into a connected server and run the resilient installer update flow.
    """
    from apps.deployments.models import ManagedServer

    from .tasks_maintenance import ThrottledLogAppender

    try:
        server = ManagedServer.objects.get(id=server_id)
    except ManagedServer.DoesNotExist:
        logger.error("Update Task: Server %s not found", server_id)
        return False

    lock_key = f"server-update:{server_id}"
    if not cache.add(lock_key, "1", timeout=7200):
        _append_remote_update_log(
            server,
            f"\n--- Update skipped at {timezone.now()} - another update is already running ---\n",
        )
        logger.warning("Update Task: duplicate update ignored for server %s", server_id)
        return False

    logger.info("Update Task: Starting update for server %s (%s)", server.name, server.host)

    server.provision_status = ManagedServer.ProvisionStatus.UPDATING
    server.save(update_fields=["provision_status", "updated_at"])
    _append_remote_update_log(
        server,
        f"\n--- Update started at {timezone.now()} for {server.name} ({server.host}) ---\n",
    )

    appender = ThrottledLogAppender(server, interval=1.5)
    def log_cb(out, err):
        if out:
            appender.append(out)
        if err:
            appender.append(err)

    try:
        from apps.deployments.services.ssh_client import SSHClient
        if not (server.ssh_key or server.ssh_password):
            raise RuntimeError("Server has no SSH credentials configured for updates.")

        ssh = SSHClient(
            ip=server.host,
            key_content=server.ssh_key,
            password=server.ssh_password,
            user=server.ssh_user,
            port=server.ssh_port,
            wg_address=server.wg_address,
        )
        ssh.connect()
        hosting_path = ssh.find_hosting_path()
        _append_remote_update_log(server, f"> Connected over SSH. install_path={hosting_path}\n\n--- Preflight ---\n")

        stdout, stderr, code = _run_ssh_command(
            ssh,
            _remote_update_preflight_script(hosting_path),
            timeout=120,
            raise_on_error=False,
            callback=log_cb,
        )
        appender.flush()
        _append_remote_update_log(server, "\n")
        if code != 0:
            raise RuntimeError(f"Remote update preflight failed with exit code {code}.")

        # The platform installer repository is public/open source, so remote
        # platform updates should use the unauthenticated default Git remote
        # instead of depending on a user's linked GitHub OAuth token.
        branch = (os.environ.get('SMSLY_BRANCH') or 'main').strip() or 'main'
        logger.info("Update Task: Triggering installer update on %s (branch: %s)", server.host, branch)

        # Build environment for the update
        config = PlatformConfig.load()
        master_ip = str(config.server_ip or os.environ.get('PUBLIC_IP') or '').strip() or '127.0.0.1'
        env_vars = {
            "NON_INTERACTIVE": "1",
            "SKIP_REBOOT": "1",
            "SMSLY_STRICT_VERIFY": "1",
            "MASTER_IP": master_ip,
            "SMSLY_BRANCH": branch,
        }
        update_args = ["--update"]

        is_lite = getattr(server, "is_lite_agent", False)
        is_primary = getattr(server, "is_primary", False)
        quoted_path = shlex.quote(hosting_path)
        quoted_branch = shlex.quote(branch)
        git_steps = (
            f"cd {quoted_path} && "
            "if [ \"$(id -u)\" -eq 0 ]; then SUDO=''; else SUDO='sudo -n'; fi; "
            "git config --global --add safe.directory \"$PWD\" 2>/dev/null || true; "
            "if [ -n \"$(git status --porcelain 2>/dev/null)\" ]; then "
            "git stash push --include-untracked -m \"remote-update-$(date +%s)\" >/dev/null 2>&1 || true; "
            "fi; "
            f"git fetch origin {quoted_branch} >/dev/null 2>&1 && "
            f"git checkout -B {quoted_branch} origin/{quoted_branch} >/dev/null 2>&1 && "
            f"git branch --set-upstream-to=origin/{quoted_branch} {quoted_branch} >/dev/null 2>&1 || true"
        )

        if is_lite:
            from apps.deployments.services.provisioner import (
                build_agent_lite_install_env,
            )

            lite_env, lite_messages = build_agent_lite_install_env(
                server,
                master_ip=master_ip,
            )
            for message in lite_messages:
                _append_remote_update_log(server, f"> {message}\n")
            env_vars.update(lite_env)
            update_args.append("--mode=agent-lite")

            env_str = " ".join([f"{k}={shlex.quote(str(v))}" for k, v in env_vars.items()])
            update_args_str = " ".join(shlex.quote(arg) for arg in update_args)
            cmd_update = (
                f"{git_steps} && "
                f"$SUDO env {env_str} bash install.sh {update_args_str}"
            )
            _append_remote_update_log(server, f"> Running lite-agent installer update (branch: {branch})...\n\n--- Installer output ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_update,
                timeout=5400,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Installer update failed with exit code {code}.")
            _append_remote_update_log(server, "\n--- Postflight ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                _remote_update_postflight_script(hosting_path),
                timeout=180,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Remote update postflight failed with exit code {code}.")
        elif is_primary:
            # Primary/master node: full install.sh --update (rebuilds everything
            # including frontend, Traefik, Caddy — master needs the full pipeline).
            env_str = " ".join([f"{k}={shlex.quote(str(v))}" for k, v in env_vars.items()])
            update_args_str = " ".join(shlex.quote(arg) for arg in update_args)
            cmd_update = (
                f"{git_steps} && "
                f"$SUDO env {env_str} bash install.sh {update_args_str}"
            )
            _append_remote_update_log(server, f"> Running master full update (branch: {branch})...\n\n--- Installer output ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_update,
                timeout=5400,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Master update failed with exit code {code}.")
            _append_remote_update_log(server, "\n--- Postflight ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                _remote_update_postflight_script(hosting_path),
                timeout=180,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Remote update postflight failed with exit code {code}.")
        else:
            # Remote full-stack node (own DB): targeted rebuild of app containers only.
            # Do NOT run install.sh --update — that restarts PG/Redis/RabbitMQ
            # which can corrupt the node's own database.
            BUILD_TIMEOUT = int(os.environ.get('SMSLY_REMOTE_BUILD_TIMEOUT', '14400'))  # 4h default
            app_services = "backend celery celery-fast celery-deploy celery-beat"
            compose_flags = "--no-cache --pull"
            cmd_build = (
                f"{git_steps} && "
                f"$SUDO docker compose -f docker-compose.prod.yml build {compose_flags} {app_services}"
            )
            cmd_up = (
                f"$SUDO docker compose -f docker-compose.prod.yml up -d --no-deps {app_services}"
            )
            _append_remote_update_log(
                server,
                f"> Remote full-stack node: rebuilding app containers (branch: {branch})...\n"
                f"> Services: {app_services}\n\n--- Build output ---\n",
            )
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_build,
                timeout=BUILD_TIMEOUT,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Container build failed with exit code {code}.")

            _append_remote_update_log(server, "> Restarting app containers...\n\n--- Restart output ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_up,
                timeout=300,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Container restart failed with exit code {code}.")

        update_fields = ["provision_status", "updated_at"]
        if getattr(server, "is_lite_agent", False):
            metadata = dict(server.provider_metadata or {})
            metadata["connection_mode"] = "agent-lite"
            metadata["node_id"] = str(server.id)
            metadata["node_host"] = str(server.host or "")
            metadata["node_queue"] = str(env_vars.get("SMSLY_NODE_QUEUE") or "")
            server.provider_metadata = metadata
            update_fields.append("provider_metadata")
            gateway_secret = str(env_vars.get("MASTER_GATEWAY_SECRET") or "").strip()
            if gateway_secret:
                server.gateway_secret = gateway_secret
                update_fields.append("gateway_secret")
        else:
            # Full-install agents: re-read GATEWAY_SECRET from the remote
            # .env to catch any changes made by the installer update.
            try:
                hosting_path = ssh.find_hosting_path()
                fresh_secret = ssh.get_gateway_secret(hosting_path)
                if isinstance(fresh_secret, str):
                    fresh_secret = fresh_secret.strip()
                else:
                    fresh_secret = ""
                if fresh_secret and server.gateway_secret != fresh_secret:
                    server.gateway_secret = fresh_secret
                    update_fields.append("gateway_secret")
                    _append_remote_update_log(
                        server,
                        "> Re-synced GATEWAY_SECRET from agent after update.\n",
                    )
            except Exception as secret_exc:
                _append_remote_update_log(
                    server,
                    f"> Warning: could not re-sync GATEWAY_SECRET: {secret_exc}\n",
                )
        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.save(update_fields=update_fields)
        _append_remote_update_log(
            server,
            f"\n--- Update completed successfully at {timezone.now()} ---\n",
        )
        if _env_bool("SMSLY_REMOTE_UPDATE_REBOOT_ON_SUCCESS", default=False):
            reboot_cmd = (
                "if [ \"$(id -u)\" -eq 0 ]; then "
                "(nohup sh -c 'sleep 8; /sbin/reboot || reboot' >/dev/null 2>&1 &); "
                "else "
                "(nohup sh -c 'sleep 8; sudo -n /sbin/reboot || sudo -n reboot' >/dev/null 2>&1 &); "
                "fi"
            )
            ssh.exec_command(reboot_cmd, timeout=10, raise_on_error=False)
            server.status = ManagedServer.Status.UNKNOWN
            server.save(update_fields=["status", "updated_at"])
            _append_remote_update_log(
                server,
                "> Remote reboot scheduled after successful update.\n",
            )
        logger.info("Update Task: Finished successfully for %s", server.host)

        # Dispatch notification to server owner when update completes
        try:
            from apps.notifications.tasks import dispatch_notification
            dispatch_notification.delay(
                event_type='server_update_success',
                user_id=server.owner.id,
                title=f"✅ Server Update Succeeded: {server.name}",
                message=f"The update process for server '{server.name}' ({server.host}) completed successfully.",
                metadata={'server_id': str(server.id), 'server_name': server.name, 'host': server.host},
            )
        except Exception as notify_exc:
            logger.warning("Failed to dispatch server update success notification: %s", notify_exc)

        return True

    except Exception as e:
        error_msg = f"Update Task failed for {server.host}: {e!s}"
        logger.error(error_msg)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_remote_update_log(server, f"\nFATAL ERROR: {e!s}\n")

        # Dispatch notification to server owner when update fails
        try:
            from apps.notifications.tasks import dispatch_notification
            dispatch_notification.delay(
                event_type='server_update_failed',
                user_id=server.owner.id,
                title=f"❌ Server Update Failed: {server.name}",
                message=f"The update process for server '{server.name}' ({server.host}) failed.\nReason: {e!s}",
                metadata={'server_id': str(server.id), 'server_name': server.name, 'host': server.host, 'error': str(e)},
            )
        except Exception as notify_exc:
            logger.warning("Failed to dispatch server update failure notification: %s", notify_exc)

        return False

    finally:
        if 'ssh' in locals():
            ssh.close()
        cache.delete(lock_key)
