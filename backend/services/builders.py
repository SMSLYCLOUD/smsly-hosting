# pylint: disable=line-too-long,logging-fstring-interpolation,subprocess-run-check,broad-exception-caught,too-many-nested-blocks,consider-using-with,too-few-public-methods,import-outside-toplevel
# pylint: disable=no-member
"""Build manager service."""
import time
import logging
import subprocess
import shutil
from pathlib import Path
from django.conf import settings
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class BuildManager:
    """
    Manages the build process (Code -> Docker Image).
    """
    def __init__(self, deployment):
        self.deployment = deployment
        self.service = deployment.service
        self.work_dir = Path(f"/tmp/builds/{self.deployment.id}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def build_image(self):
        """
        Builds a Docker image from the repo with real-time log streaming.
        """
        registry = settings.CONTAINER_REGISTRY_URL
        image_tag = f"{registry}/{self.service.name}:{self.deployment.commit_hash[:7]}"
        logger.info(f"Starting build for {self.service.name} commit {self.deployment.commit_hash}")

        try:
            # 0. Login (if configured)
            if settings.REGISTRY_USER and settings.REGISTRY_PASSWORD:
                self._log(f"Logging in to {registry}...")
                # Pipe password via stdin for security
                login_proc = subprocess.Popen(
                    ["docker", "login", registry, "-u", settings.REGISTRY_USER, "--password-stdin"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                _, stderr = login_proc.communicate(input=settings.REGISTRY_PASSWORD)
                if login_proc.returncode != 0:
                    self._log(f"Login failed: {stderr}")
                    # Don't raise, try pushing anyway (might be using credential helper)
                else:
                    self._log("Login successful.")

            # 1. Clone
            self._log(f"Cloning repository {self.service.repository_url}...")
            if self.work_dir.exists():
                shutil.rmtree(self.work_dir)

            self._run_command(
                ["git", "clone", "--branch", self.service.branch, self.service.repository_url, str(self.work_dir)]
            )

            # 2. Build
            dockerfile_path = self.work_dir / self.service.root_directory.strip("/")
            self._log(f"Building Docker image from {dockerfile_path}...")

            if self.service.build_command:
                self._log(f"NOTE: build_command '{self.service.build_command}' is ignored in Dockerfile mode. Only applicable for Buildpacks.")

            self._run_command(
                ["docker", "build", "-t", image_tag, "."],
                cwd=str(dockerfile_path)
            )

            # 3. Push
            self._log(f"Pushing image to {image_tag}...")
            # Note: Requires docker login to be handled in the base image or host environment
            self._run_command(["docker", "push", image_tag])

            # 4. Security Scan (Simulated Trivy)
            self._run_security_scan(image_tag)

            self._log("Build and Push successful.")
            return image_tag

        except subprocess.CalledProcessError as e:
            self._log(f"Command failed with return code {e.returncode}")
            raise e
        except Exception as e:
            self._log(f"Build failed: {str(e)}")
            raise e
        finally:
            # Cleanup
            if self.work_dir.exists():
                shutil.rmtree(self.work_dir)

    def _run_security_scan(self, image_tag):
        """
        Run Trivy vulnerability scan on the Docker image.
        Falls back to skip if Trivy is not installed.
        """
        self._log(f"Running vulnerability scan on {image_tag}...")

        try:
            # Check if Trivy is available
            result = subprocess.run(
                ["trivy", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                self._log("Trivy not available. Skipping security scan.")
                return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._log("Trivy not installed. Skipping security scan.")
            return

        try:
            # Run Trivy scan with JSON output
            result = subprocess.run(
                [
                    "trivy", "image",
                    "--format", "json",
                    "--severity", "CRITICAL,HIGH,MEDIUM,LOW",
                    "--timeout", "5m",
                    image_tag
                ],
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )

            if result.returncode == 0 or result.stdout:
                import json
                try:
                    scan_results = json.loads(result.stdout)
                    vulns = {"critical": 0, "high": 0, "medium": 0, "low": 0}

                    # Parse vulnerability counts from Trivy JSON output
                    for result_item in scan_results.get("Results", []):
                        for vuln in result_item.get("Vulnerabilities", []):
                            severity = vuln.get("Severity", "").lower()
                            if severity in vulns:
                                vulns[severity] += 1

                    # Store full report for detailed view
                    self.deployment.vulnerability_report = {
                        "summary": vulns,
                        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "image": image_tag
                    }
                    self.deployment.save(update_fields=['vulnerability_report'])

                    # Log summary
                    self._log(f"Scan complete: {vulns['critical']} critical, {vulns['high']} high, {vulns['medium']} medium, {vulns['low']} low")

                    if vulns['critical'] > 0:
                        self._log(f"WARNING: Found {vulns['critical']} CRITICAL vulnerabilities!")
                    else:
                        self._log("Security scan passed (no critical issues).")

                except json.JSONDecodeError:
                    self._log("Failed to parse Trivy output. Raw output saved.")
                    self.deployment.vulnerability_report = {"error": "Parse failed", "raw": result.stdout[:1000]}
                    self.deployment.save(update_fields=['vulnerability_report'])
            else:
                self._log(f"Trivy scan failed: {result.stderr[:500]}")

        except subprocess.TimeoutExpired:
            self._log("Security scan timed out after 10 minutes.")
        except Exception as e:
            self._log(f"Security scan error: {str(e)}")

    def _run_command(self, cmd, cwd=None):
        """Run command and stream output to logs."""
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Merge stderr into stdout
            cwd=cwd,
            universal_newlines=True,
            bufsize=1 # Line buffered
        )

        for line in process.stdout:
            self._log(line.strip(), timestamp=False)

        process.wait()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)

    def _log(self, message, timestamp=True):
        """Append logs to the deployment atomically to avoid race conditions."""
        from django.db.models import Value
        from django.db.models.functions import Concat
        from apps.deployments.models import Deployment

        prefix = f"[{time.strftime('%H:%M:%S')}] " if timestamp else ""
        log_line = f"{prefix}{message}\n"

        # Atomic append using Concat to avoid race condition
        Deployment.objects.filter(id=self.deployment.id).update(
            build_logs=Concat('build_logs', Value(log_line))
        )
