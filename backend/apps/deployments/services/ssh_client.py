import paramiko
import time
import socket
import logging
import io
import os
import re
import shlex
import warnings

logger = logging.getLogger(__name__)


class SSHConnectionError(Exception):
    """Raised when SSH command execution fails."""


class SSHClient:
    def __init__(
        self,
        ip=None,
        key_content='',
        user='root',
        password='',
        port=22,
        host=None,
        username=None,
        private_key=None,
    ):
        self.ip = ip or host
        self.port = port
        self.user = username or user
        self.key_content = key_content or private_key or ''
        self.password = password
        self.client = None
        self.sftp = None
        self._key = None

    def _load_key(self):
        if self._key:
            return self._key

        # Validate the content looks like a key before trying to parse it
        trimmed = self.key_content.strip()
        if not trimmed.startswith("-----BEGIN "):
            raise ValueError(
                "Key content does not look like a valid private key "
                "(must start with '-----BEGIN ...')"
            )

        # Try various key formats
        errors = []
        for key_cls in (
            paramiko.RSAKey,
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
        ):
            try:
                self._key = key_cls.from_private_key(io.StringIO(trimmed))
                return self._key
            except Exception as e:
                errors.append(f"{key_cls.__name__}: {e}")

        raise ValueError(
            f"Could not load private key (tried {len(errors)} formats). "
            f"Errors: {'; '.join(errors)}"
        )

    def connect(self):
        if self.client:
            return

        self.client = paramiko.SSHClient()
        # Do NOT call load_system_host_keys() in Docker containers — stale
        # entries from prior provisioning runs cause key-mismatch rejections
        # after server reboots (AutoAddPolicy only handles *missing* keys, not
        # *changed* ones).  WarningPolicy logs but accepts any host key, which
        # is appropriate for infrastructure automation inside a trusted network.

        strict_mode = str(os.environ.get("SMSLY_STRICT_SSH_HOST_KEY_CHECK", "true")).lower() not in ("false", "0", "no")
        if strict_mode:
            # We enforce host key checking by default. The key should either be added to known_hosts
            # or the user has explicitly allowed the first-trust policy.
            # However, since cloud provisioning typically requires TOFU (Trust On First Use),
            # we use AutoAddPolicy but log a warning if strict mode is disabled but not fully supported in this context.
            # To actually fix SEC-ZT-002, we respect strict mode. If strict mode is ON, we use RejectPolicy.
            allow_auto_add = str(os.environ.get("ALLOW_SSH_AUTOADD", "false")).lower() in ("true", "1", "yes")
            if allow_auto_add:
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                warnings.warn("Using AutoAddPolicy due to ALLOW_SSH_AUTOADD=true")
            else:
                self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            self.client.set_missing_host_key_policy(paramiko.WarningPolicy())
            warnings.warn("Strict SSH host key checking is disabled. This is insecure!")


        # Determine auth method: key or password
        connect_kwargs = {
            'hostname': self.ip,
            'port': self.port,
            'username': self.user,
            'timeout': 10,
            'banner_timeout': 30,
            'auth_timeout': 20,
            'look_for_keys': False,
            'allow_agent': False,
            'compress': True,
        }

        if self.key_content and self.key_content.strip():
            try:
                key = self._load_key()
                connect_kwargs['pkey'] = key
            except (ValueError, TypeError) as e:
                logger.warning(
                    "SSH key content is present but invalid (%s); "
                    "falling back to password auth if available.",
                    e,
                )
                if self.password:
                    connect_kwargs.pop('pkey', None)
                    connect_kwargs['password'] = self.password
                else:
                    raise ValueError(
                        "SSH key is invalid and no password fallback available."
                    ) from e
        elif self.password:
            connect_kwargs['password'] = self.password
        else:
            raise ValueError("No SSH key or password provided.")

        retries = 3
        last_exc = None
        for i in range(retries):
            try:
                self.client.connect(**connect_kwargs)
                transport = self.client.get_transport()
                if transport:
                    transport.set_keepalive(30)
                return
            except paramiko.AuthenticationException:
                raise
            except (socket.error, socket.timeout, TimeoutError, paramiko.SSHException) as e:
                last_exc = e
                if i < retries - 1:
                    time.sleep(2)

        raise SSHConnectionError(
            f"SSH connection to {self.ip}:{self.port} failed after {retries} attempts: {last_exc}"
        )

    def close(self):
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.sftp = None
        self.client = None

    def exec_command(self, command, timeout=None, raise_on_error=True):
        if not self.client:
            self.connect()

        logger.debug(f"SSH Exec ({self.ip}): {command}")
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        stdin.close()

        # Drain stdout/stderr while the command runs. Waiting for exit before
        # reading can deadlock on noisy commands once Paramiko's channel window
        # fills up.
        channel = stdout.channel
        out_chunks = []
        err_chunks = []
        deadline = time.monotonic() + timeout if timeout else None
        timed_out = False

        while True:
            while channel.recv_ready():
                out_chunks.append(channel.recv(65536))
            while channel.recv_stderr_ready():
                err_chunks.append(channel.recv_stderr(65536))

            if channel.exit_status_ready():
                break

            if deadline and time.monotonic() > deadline:
                timed_out = True
                channel.close()
                break

            time.sleep(0.05)

        while channel.recv_ready():
            out_chunks.append(channel.recv(65536))
        while channel.recv_stderr_ready():
            err_chunks.append(channel.recv_stderr(65536))

        out = b"".join(out_chunks).decode('utf-8', errors='replace')
        err = b"".join(err_chunks).decode('utf-8', errors='replace')

        if timed_out:
            exit_status = 124
            err = (err + f"\nCommand timed out after {timeout} seconds.").strip()
        else:
            exit_status = channel.recv_exit_status()

        if exit_status != 0 and raise_on_error:
            raise SSHConnectionError(f"Command failed (exit {exit_status}): {err or out}")

        return out, err, exit_status

    def upload_file(self, local_path, remote_path):
        if not self.client:
            self.connect()
        if not self.sftp:
            self.sftp = self.client.open_sftp()

        logger.info(f"Uploading {local_path} to {self.ip}:{remote_path}")
        self.sftp.put(local_path, remote_path)

    def download_file(self, remote_path, local_path):
        if not self.client:
            self.connect()
        if not self.sftp:
            self.sftp = self.client.open_sftp()

        logger.info(f"Downloading {self.ip}:{remote_path} to {local_path}")
        self.sftp.get(remote_path, local_path)

    def check_docker(self):
        try:
            out, err, code = self.exec_command("docker --version")
            return code == 0
        except Exception:
            return False

    def install_docker(self):
        logger.info(f"Installing Docker on {self.ip}...")
        # Simple comprehensive install script
        # Check if curl is available, install it if not (apt/yum)
        check_curl = "which curl || (apt-get update && apt-get install -y curl) || (yum install -y curl)"
        try:
            self.exec_command(check_curl)
        except Exception:
            pass  # Best effort

        cmd = "curl -fsSL https://get.docker.com | sh"
        self.exec_command(cmd)

    def find_hosting_path(self):
        """Try to find the SMSLY Hosting installation directory on the remote."""
        candidates = ["/opt/smsly-hosting", "/opt/smsly", "/app"]
        for path in candidates:
            try:
                # Check if docker-compose.prod.yml or .env exists in this path
                self.exec_command(f"test -f {shlex.quote(path)}/.env", timeout=10)
                return path
            except Exception:
                continue
        
        # Fallback: try to find it via locate or find if available
        try:
            out, _err, code = self.exec_command(
                "found=$(find /opt -maxdepth 3 -name .env -print -quit 2>/dev/null); "
                "[ -n \"$found\" ] && dirname \"$found\"",
                timeout=15,
                raise_on_error=False,
            )
            path = out.strip().splitlines()[0] if code == 0 and out.strip() else ""
            if path:
                return path
        except Exception:
            pass
            
        return candidates[0]  # Default to /opt/smsly-hosting

    def run_diagnose_nodes_fix(self, hosting_path):
        """Run the diagnose_nodes --fix command and return the output."""
        # Try both 'docker compose' and 'docker-compose'
        quoted_path = shlex.quote(hosting_path)
        cmd = f"cd {quoted_path} && (docker compose exec -T backend python manage.py diagnose_nodes --fix || docker-compose exec -T backend python manage.py diagnose_nodes --fix)"
        out, err, code = self.exec_command(cmd)
        return out + err

    def create_api_token(self, hosting_path):
        """Create an API token via drf_create_token and return it."""
        quoted_path = shlex.quote(hosting_path)

        api_token_shell = (
            "from django.contrib.auth import get_user_model; "
            "from apps.deployments.api_token_auth import APIToken; "
            "User=get_user_model(); "
            "u=User.objects.filter(is_superuser=True,is_active=True).first(); "
            "assert u, 'no active superuser'; "
            "APIToken.objects.filter(user=u,name='node:auto-ssh',is_active=True).update(is_active=False); "
            "obj, raw=APIToken.create_token(u, name='node:auto-ssh'); "
            "print('SMSLY_TOKEN: '+raw)"
        )
        compose_prefixes = (
            "docker compose exec -T backend",
            "docker-compose exec -T backend",
        )

        try:
            for prefix in compose_prefixes:
                cmd = (
                    f"cd {quoted_path} && {prefix} python manage.py shell -c "
                    f"{shlex.quote(api_token_shell)} 2>&1"
                )
                out, err, _code = self.exec_command(cmd, raise_on_error=False)
                raw = (out or "") + (err or "")
                m = re.search(r"SMSLY_TOKEN:\s*(smsly_[A-Za-z0-9_]+)", raw)
                if m:
                    return m.group(1)

            # Strategy 2: use DRF's drf_create_token command for older nodes.
            cmds = [
                f"cd {quoted_path}",
                "docker compose exec -T backend python manage.py drf_create_token admin 2>&1",
            ]
            cmd = " && ".join(cmds)
            out, err, code = self.exec_command(cmd, raise_on_error=False)
            raw = (out or "") + (err or "")
            if code != 0:
                out, err, code = self.exec_command(
                    f"cd {quoted_path} && docker-compose exec -T backend python manage.py drf_create_token admin 2>&1",
                    raise_on_error=False,
                )
                raw = (out or "") + (err or "")

            for line in raw.splitlines():
                line = line.strip()
                if "Key:" in line:
                    token = line.split("Key:")[-1].strip()
                    if token:
                        return token
                if len(line) == 40 and line.isalnum():
                    return line

            # Strategy 3: create DRF token directly via Django ORM shell.
            logger.info("drf_create_token failed, trying Django ORM fallback")
            shell_cmd = (
                f"cd {quoted_path} && "
                f"docker compose exec -T backend python manage.py shell -c "
                f"'from rest_framework.authtoken.models import Token; "
                f"from django.contrib.auth import get_user_model; "
                f"User = get_user_model(); "
                f"u = User.objects.filter(is_superuser=True).first(); "
                f"if not u: u = User.objects.create_superuser(username=\"admin\", email=\"admin@example.com\", password=None); "
                f"tok, _ = Token.objects.get_or_create(user=u); "
                f"print(\"TOKEN: \" + tok.key)' 2>&1"
            )
            out2, err2, code2 = self.exec_command(shell_cmd, raise_on_error=False)
            raw2 = (out2 or "") + (err2 or "")
            m = re.search(r"TOKEN:\s+(\w+)", raw2)
            if m:
                return m.group(1)
        except Exception as exc:
            logger.warning("create_api_token failed: %s", exc)
        return None

    def get_gateway_secret(self, hosting_path):
        """Extract the GATEWAY_SECRET from the remote .env file."""
        try:
            env_path = shlex.quote(f"{hosting_path.rstrip('/')}/.env")
            cmd = f"sed -n 's/^GATEWAY_SECRET=//p' {env_path} | head -n 1"
            out, err, code = self.exec_command(cmd)
            secret = out.strip().strip("'\"")
            if secret:
                return secret
        except Exception:
            pass
        return None
