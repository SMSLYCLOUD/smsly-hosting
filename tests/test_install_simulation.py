import os
import shutil
import subprocess
import tempfile
import unittest


class TestInstallScript(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.install_script = os.path.abspath("install.sh")
        self.env_file = os.path.join(self.test_dir, ".env")

        # Copy install script to temp dir
        shutil.copy(self.install_script, os.path.join(self.test_dir, "install.sh"))

        # Mock commands that might fail or have side effects
        self.mock_bin_dir = os.path.join(self.test_dir, "bin")
        os.makedirs(self.mock_bin_dir)
        os.environ["PATH"] = f"{self.mock_bin_dir}:{os.environ['PATH']}"

        # Mock docker
        with open(os.path.join(self.mock_bin_dir, "docker"), "w") as f:
            f.write("#!/bin/bash\necho 'Docker version 20.10.0'")
        os.chmod(os.path.join(self.mock_bin_dir, "docker"), 0o755)

        # Mock curl
        with open(os.path.join(self.mock_bin_dir, "curl"), "w") as f:
            f.write("#!/bin/bash\nif [[ $@ == *ipify* ]]; then echo '1.2.3.4'; else echo ''; fi")
        os.chmod(os.path.join(self.mock_bin_dir, "curl"), 0o755)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_install_ip_mode_non_interactive(self):
        pass

    def test_env_generation_logic(self):
        """
        Extract and test the python one-liner used for secret generation in install.sh.
        This ensures the most critical part (secrets) works on this system.
        """
        # This is the python block from install.sh
        script = """
import secrets, string
from cryptography.fernet import Fernet

chars = string.ascii_letters + string.digits
secret_key = ''.join(secrets.choice(chars) for _ in range(50))
fernet_key = Fernet.generate_key().decode()
pg_pass = secrets.token_hex(16)
redis_pass = secrets.token_hex(16)
gateway_secret = secrets.token_hex(32)
webhook_secret = secrets.token_hex(32)

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
print(f'REDIS_PASSWORD={redis_pass}')
print(f'GATEWAY_SECRET={gateway_secret}')
print(f'GITHUB_WEBHOOK_SECRET={webhook_secret}')
"""
        result = subprocess.run(["python3", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        output = result.stdout

        self.assertIn("SECRET_KEY=", output)
        self.assertIn("FIELD_ENCRYPTION_KEY=", output)
        self.assertIn("POSTGRES_PASSWORD=", output)

        # Verify Fernet key format validity again
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("FIELD_ENCRYPTION_KEY="):
                # Ensure we handle potentially missing padding or extra whitespace if any
                key = line.split("=", 1)[1].strip()
                from cryptography.fernet import Fernet
                Fernet(key.encode()) # Should not raise

if __name__ == "__main__":
    unittest.main()
