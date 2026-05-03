import paramiko
import time
import socket
import logging
import io
import os

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
        # Enforce host key verification by default, but allow AutoAdd for convenience
        # as many users don't pre-populate known_hosts in a containerized environment.
        allow_autoadd = str(os.environ.get("ALLOW_SSH_AUTOADD", "true")).lower() in {
            "1", "true", "yes", "on"
        }
        if allow_autoadd:
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())

        # Determine auth method: key or password
        connect_kwargs = {
            'hostname': self.ip,
            'port': self.port,
            'username': self.user,
            'timeout': 10,
            'banner_timeout': 30,
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
                return
            except (socket.error, paramiko.SSHException) as e:
                last_exc = e
                if i < retries - 1:
                    time.sleep(2)

        raise last_exc

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

    def exec_command(self, command, timeout=None):
        if not self.client:
            self.connect()

        logger.debug(f"SSH Exec ({self.ip}): {command}")
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)

        # Wait for command to complete
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')

        if exit_status != 0:
            raise SSHConnectionError(f"Command failed (exit {exit_status}): {err or out}")

        return out

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
            self.exec_command("docker --version")
            return True
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
                self.exec_command(f"ls {path}/.env")
                return path
            except Exception:
                continue
        
        # Fallback: try to find it via locate or find if available
        try:
            path = self.exec_command("find /opt -name '.env' -maxdepth 3 | xargs dirname | head -n 1").strip()
            if path:
                return path
        except Exception:
            pass
            
        return candidates[0]  # Default to /opt/smsly-hosting

    def run_diagnose_nodes_fix(self, hosting_path):
        """Run the diagnose_nodes --fix command and return the output."""
        # Try both 'docker compose' and 'docker-compose'
        cmd = f"cd {hosting_path} && (docker compose exec -T backend python manage.py diagnose_nodes --fix || docker-compose exec -T backend python manage.py diagnose_nodes --fix)"
        return self.exec_command(cmd)

    def get_gateway_secret(self, hosting_path):
        """Extract the GATEWAY_SECRET from the remote .env file."""
        try:
            cmd = f"grep GATEWAY_SECRET {hosting_path}/.env | cut -d= -f2"
            secret = self.exec_command(cmd).strip().strip("'\"")
            if secret:
                return secret
        except Exception:
            pass
        return None
