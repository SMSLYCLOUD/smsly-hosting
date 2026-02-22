import paramiko
import time
import socket
import logging
import io
import os

logger = logging.getLogger(__name__)

class SSHClient:
    def __init__(self, ip, key_content, user='root'):
        self.ip = ip
        self.user = user
        self.key_content = key_content
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

        key = self._load_key()
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        retries = 3
        last_exc = None
        for i in range(retries):
            try:
                self.client.connect(
                    hostname=self.ip,
                    username=self.user,
                    pkey=key,
                    timeout=10,
                    banner_timeout=30
                )
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
            raise Exception(f"Command failed (exit {exit_status}): {err or out}")

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
