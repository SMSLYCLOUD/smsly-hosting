import paramiko
import time
import socket
import logging
import io
import os
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

        # Try various key formats
        errors = []
        try:
            self._key = paramiko.RSAKey.from_private_key(io.StringIO(self.key_content))
            return self._key
        except Exception as e:
            errors.append(f"RSA: {e}")

        try:
            self._key = paramiko.Ed25519Key.from_private_key(io.StringIO(self.key_content))
            return self._key
        except Exception as e:
            errors.append(f"Ed25519: {e}")

        try:
            self._key = paramiko.ECDSAKey.from_private_key(io.StringIO(self.key_content))
            return self._key
        except Exception as e:
            errors.append(f"ECDSA: {e}")

        # Finally try generic PKey (might handle others)
        try:
            self._key = paramiko.PKey.from_private_key(io.StringIO(self.key_content))
            return self._key
        except Exception as e:
            errors.append(f"Generic: {e}")

        raise ValueError(f"Could not load private key. Errors: {'; '.join(errors)}")

    def connect(self):
        if self.client:
            return

        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        # AutoAddPolicy saves unknown host keys to known_hosts on first connection.
        # Subsequent connections verify against the saved key (load_system_host_keys).
        # The env-var-based approach is unreliable because container env vars are set
        # at startup and don't hot-reload when .env changes on the host.
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

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

        if self.key_content:
            key = self._load_key()
            connect_kwargs['pkey'] = key
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
