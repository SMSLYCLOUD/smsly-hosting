import contextlib
import io
import logging
import os
import re
import shlex
import time

import paramiko

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
        wg_address=None,
    ):
        self.ip = ip or host
        self.port = port
        self.user = username or user
        self.key_content = key_content or private_key or ''
        self.password = password
        self.wg_address = wg_address
        self.client = None
        self.sftp = None
        self._key = None

    def _load_key(self):
        if self._key:
            return self._key

        # Validate the content looks like a key before trying to parse it
        trimmed = self.key_content.strip()
        if not trimmed.startswith("-----BEGIN "):
            # Defensive: if the stored value is raw base64 key body without PEM
            # headers (legacy bug), try wrapping it so existing DB entries work.
            recovered = self._try_recover_pem(trimmed)
            if recovered:
                trimmed = recovered
            else:
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

    @staticmethod
    def _try_recover_pem(raw: str) -> str | None:
        """Try to wrap raw base64 key body in PEM headers (legacy data fix).

        Returns a PEM-formatted string if the raw content decodes to a
        plausible private key, otherwise None.
        """
        import base64
        # Strip any whitespace/newlines and attempt base64 decode
        candidate = raw.replace('\n', '').replace('\r', '').replace(' ', '')
        try:
            decoded = base64.b64decode(candidate)
        except Exception:
            return None
        # DER-encoded RSA private key starts with SEQUENCE tag 0x30
        # Ed25519 private key is 32-64 bytes; PKCS8 is longer.
        # A reasonable minimum is 32 bytes for any key type.
        if len(decoded) < 32:
            return None
        # Try wrapping as RSA (most common), then generic PKCS8
        for label in ("RSA PRIVATE KEY", "PRIVATE KEY"):
            pem = f"-----BEGIN {label}-----\n"
            # Insert newlines every 64 chars (standard PEM line width)
            for i in range(0, len(candidate), 64):
                pem += candidate[i : i + 64] + "\n"
            pem += f"-----END {label}-----\n"
            try:
                # Quick validation: attempt to parse it
                key_file = io.StringIO(pem)
                paramiko.RSAKey.from_private_key(key_file)
                return pem
            except Exception:
                pass
            try:
                key_file = io.StringIO(pem)
                paramiko.Ed25519Key.from_private_key(key_file)
                return pem
            except Exception:
                pass
        return None

    def connect(self):
        if self.client:
            return

        self.client = paramiko.SSHClient()
        # Do NOT call load_system_host_keys() in Docker containers — stale
        # entries from prior provisioning runs cause key-mismatch rejections
        # after server reboots (AutoAddPolicy only handles *missing* keys, not
        # *changed* ones).  WarningPolicy logs but accepts any host key, which
        # is appropriate for infrastructure automation inside a trusted network.

        strict_mode = str(os.environ.get("SMSLY_STRICT_SSH_HOST_KEY_CHECK", "false")).lower() not in ("false", "0", "no")
        allow_auto_add = str(os.environ.get("ALLOW_SSH_AUTOADD", "false")).lower() in ("true", "1", "yes")

        if strict_mode and not allow_auto_add:
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif allow_auto_add:
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            logger.warning("Using AutoAddPolicy for SSH host %s (Trust On First Use). Set SMSLY_STRICT_SSH_HOST_KEY_CHECK=true for production.", self.ip)
        else:
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            logger.warning("Strict SSH host key checking is disabled for %s. Using AutoAddPolicy (insecure — set SMSLY_STRICT_SSH_HOST_KEY_CHECK=true).", self.ip)


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

        # Connect to self.ip first, then self.wg_address if provided and self.ip failed
        hosts_to_try = [self.ip]
        if self.wg_address and self.wg_address != self.ip:
            hosts_to_try.append(self.wg_address)

        for host in hosts_to_try:
            connect_kwargs['hostname'] = host
            for i in range(retries):
                try:
                    logger.info("SSHClient: Connecting to %s:%s (try %d)", host, self.port, i+1)
                    self.client.connect(**connect_kwargs)
                    transport = self.client.get_transport()
                    if transport:
                        transport.set_keepalive(30)
                    self.ip = host  # Update self.ip to the successful one
                    logger.info("SSHClient: Connected to %s:%s successfully", host, self.port)
                    return
                except paramiko.AuthenticationException:
                    raise
                except (OSError, TimeoutError, paramiko.SSHException) as e:
                    last_exc = e
                    if i < retries - 1:
                        time.sleep(2)

        raise SSHConnectionError(
            f"SSH connection to {self.ip}:{self.port} failed after trying {hosts_to_try}: {last_exc}"
        )

    def close(self):
        if self.sftp:
            with contextlib.suppress(Exception):
                self.sftp.close()
        if self.client:
            with contextlib.suppress(Exception):
                self.client.close()
        self.sftp = None
        self.client = None

    def exec_command(self, command, timeout=None, raise_on_error=True, callback=None):
        if self.client:
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                self.close()

        if not self.client:
            self.connect()

        logger.debug(f"SSH Exec ({self.ip}): {command}")
        stdin, stdout, _stderr = self.client.exec_command(command, timeout=timeout)
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
            new_out = []
            new_err = []
            while channel.recv_ready():
                chunk = channel.recv(65536)
                out_chunks.append(chunk)
                new_out.append(chunk)
            while channel.recv_stderr_ready():
                chunk = channel.recv_stderr(65536)
                err_chunks.append(chunk)
                new_err.append(chunk)

            if callback and (new_out or new_err):
                try:
                    out_str = b"".join(new_out).decode('utf-8', errors='replace')
                    err_str = b"".join(new_err).decode('utf-8', errors='replace')
                    callback(out_str, err_str)
                except Exception as cb_err:
                    logger.warning("SSH callback error: %s", cb_err)

            if channel.exit_status_ready():
                break

            if deadline and time.monotonic() > deadline:
                timed_out = True
                channel.close()
                break

            time.sleep(0.05)

        new_out_final = []
        new_err_final = []
        while channel.recv_ready():
            chunk = channel.recv(65536)
            out_chunks.append(chunk)
            new_out_final.append(chunk)
        while channel.recv_stderr_ready():
            chunk = channel.recv_stderr(65536)
            err_chunks.append(chunk)
            new_err_final.append(chunk)

        if callback and (new_out_final or new_err_final):
            try:
                out_str = b"".join(new_out_final).decode('utf-8', errors='replace')
                err_str = b"".join(new_err_final).decode('utf-8', errors='replace')
                callback(out_str, err_str)
            except Exception as cb_err:
                logger.warning("SSH callback error (final): %s", cb_err)

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
        if self.client:
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                self.close()

        if not self.client:
            self.connect()
        if not self.sftp:
            self.sftp = self.client.open_sftp()

        logger.info(f"Uploading {local_path} to {self.ip}:{remote_path}")
        self.sftp.put(local_path, remote_path)

    def download_file(self, remote_path, local_path):
        if self.client:
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                self.close()

        if not self.client:
            self.connect()
        if not self.sftp:
            self.sftp = self.client.open_sftp()

        logger.info(f"Downloading {self.ip}:{remote_path} to {local_path}")
        self.sftp.get(remote_path, local_path)

    def check_docker(self):
        try:
            _out, _err, code = self.exec_command("docker --version")
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
        out, err, _code = self.exec_command(cmd)
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
            out2, err2, _code2 = self.exec_command(shell_cmd, raise_on_error=False)
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
            out, _err, _code = self.exec_command(cmd)
            secret = out.strip().strip("'\"")
            if secret:
                return secret
        except Exception:
            pass
        return None

    def restart_stack(self, hosting_path=None):
        """Restart the entire docker-compose stack on the remote node.

        Returns (success: bool, output: str).
        """
        if not hosting_path:
            hosting_path = self.find_hosting_path()
        quoted = shlex.quote(hosting_path)

        cmd = (
            f"cd {quoted} && "
            "(docker compose up -d 2>&1 "
            "|| docker-compose up -d 2>&1)"
        )
        try:
            out, err, code = self.exec_command(
                cmd, timeout=180, raise_on_error=False,
            )
            combined = (out or "") + (err or "")
            return code == 0, combined
        except Exception as exc:
            return False, str(exc)

    def restart_backend(self, hosting_path=None):
        """Restart just the backend container on the remote node.

        Attempts 'docker compose restart backend' first, then falls back to
        'docker compose up -d backend' if the container is stopped entirely.

        Returns (success: bool, output: str).
        """
        if not hosting_path:
            hosting_path = self.find_hosting_path()
        quoted = shlex.quote(hosting_path)

        # Attempt 1: restart (works if backend is running or paused)
        restart_cmd = (
            f"cd {quoted} && "
            "(docker compose restart backend 2>&1 "
            "|| docker-compose restart backend 2>&1)"
        )
        try:
            out, err, code = self.exec_command(
                restart_cmd, timeout=60, raise_on_error=False,
            )
            combined = (out or "") + (err or "")
            if code == 0:
                return True, combined
        except Exception as exc:
            combined = str(exc)

        # Attempt 2: up -d (works if container is stopped/removed)
        up_cmd = (
            f"cd {quoted} && "
            "(docker compose up -d backend 2>&1 "
            "|| docker-compose up -d backend 2>&1)"
        )
        try:
            out, err, code = self.exec_command(
                up_cmd, timeout=120, raise_on_error=False,
            )
            combined = (out or "") + (err or "")
            if code == 0:
                return True, combined
        except Exception as exc:
            combined = str(exc)

        return False, combined
