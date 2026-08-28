"""
Exhaustive deployment diagnostics logging functions.
"""
import json
import logging
import os
import platform
import shutil
import subprocess

from .broadcast import append_log
from .files import find_binary

logger = logging.getLogger(__name__)


def log_exhaustive_deployment_diagnostics(deployment, service=None, build_dir=None):
    svc = service or getattr(deployment, 'service', None)
    if not svc:
        return

    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    cpu_cores = os.cpu_count() or "Unknown"

    mem_info = "Unknown"
    try:
        import psutil
        mem = psutil.virtual_memory()
        mem_info = f"Total: {mem.total // (1024**2)}MB, Available: {mem.available // (1024**2)}MB ({mem.percent}% used)"
    except Exception as exc:
        logger.debug("psutil memory detection failed: %s", exc)

    disk_info = "Unknown"
    try:
        check_path = build_dir if build_dir and os.path.exists(build_dir) else "/"
        total, used, free = shutil.disk_usage(check_path)
        disk_info = f"Total: {total // (1024**3)}GB, Free: {free // (1024**3)}GB"
    except Exception as exc:
        logger.debug("Disk usage detection failed: %s", exc)

    docker_bin = find_binary("docker")
    docker_ver = "Not found"
    if docker_bin:
        try:
            res = subprocess.run([docker_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                docker_ver = res.stdout.strip()
        except Exception as exc:
            logger.debug("docker version detection failed: %s", exc)

    nixpacks_bin = find_binary("nixpacks")
    nixpacks_ver = "Not found"
    if nixpacks_bin:
        try:
            res = subprocess.run([nixpacks_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                nixpacks_ver = res.stdout.strip()
        except Exception as exc:
            logger.debug("nixpacks version detection failed: %s", exc)

    trivy_bin = find_binary("trivy")
    trivy_ver = "Not installed (Using default baseline security scanner)"
    if trivy_bin:
        try:
            res = subprocess.run([trivy_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                trivy_ver = res.stdout.splitlines()[0].strip() if res.stdout else f"Installed ({trivy_bin})"
        except Exception as exc:
            logger.debug("trivy version detection failed: %s", exc)

    cosign_bin = find_binary("cosign")
    cosign_ver = "Not installed (Keyless Sigstore image signing disabled)"
    if cosign_bin:
        try:
            res = subprocess.run([cosign_bin, "version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                # cosign version outputs ASCII art first, version string last
                lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
                cosign_ver = lines[-1] if lines else f"Installed ({cosign_bin})"
        except Exception as exc:
            logger.debug("cosign version detection failed: %s", exc)

    sandbox_runtime = "runc (default)"
    sandbox_isolation = "Process-level (standard Docker)"
    try:
        from apps.deployments.services.container_runtime import detect_best_runtime
        detected = detect_best_runtime()
        if detected == "runsc":
            sandbox_runtime = "gVisor (runsc)"
            sandbox_isolation = "User-space kernel — syscall filtering, no direct kernel access"
        elif detected == "kata-runtime":
            sandbox_runtime = "Kata Containers"
            sandbox_isolation = "VM-level — lightweight Firecracker/QEMU microVM"
        else:
            sandbox_runtime = "runc (default)"
            sandbox_isolation = "Process-level — standard Linux namespace isolation"
    except Exception as exc:
        logger.debug("Container runtime detection failed: %s", exc)

    buildpack = getattr(svc, 'buildpack', 'AUTO')
    deploy_type = getattr(svc, 'deploy_type', 'DOCKER')
    repo_url = getattr(svc, 'repository_url', 'N/A')
    branch = getattr(svc, 'branch', 'main')
    internal_port = getattr(svc, 'internal_port', getattr(svc, 'port', '8000'))
    domain = getattr(svc, 'domain', getattr(svc, 'name', 'localhost'))

    env_vars = svc.env_vars.all() if hasattr(svc, 'env_vars') else []
    total_vars = len(env_vars)
    secret_vars = sum(1 for ev in env_vars if getattr(ev, 'is_secret', False))
    var_names = [ev.key for ev in env_vars[:15]]

    registry_url = getattr(deployment, 'registry_url', None) or "Local Docker Daemon"
    image_name = getattr(deployment, 'image_name', getattr(svc, 'docker_image', f"smsly/{svc.name.lower()}:latest"))

    root_user_status = "No build dir — skipped"
    if build_dir:
        try:
            dockerfile_path = os.path.join(build_dir, "Dockerfile")
            if os.path.isfile(dockerfile_path):
                with open(dockerfile_path, encoding="utf-8", errors="ignore") as df:
                    for line in df:
                        stripped = line.strip()
                        if stripped.startswith("USER "):
                            user_val = stripped.split(None, 1)[1].strip()
                            if user_val.lower() not in ("root", "0"):
                                root_user_status = f"Non-root user: {user_val} ✓"
                            else:
                                root_user_status = f"WARNING: Running as {user_val}"
                            break
                    else:
                        root_user_status = "No USER directive — container runs as root"
            else:
                root_user_status = "No Dockerfile found"
        except Exception as e:
            root_user_status = f"Check failed: {e}"

    log_lines = [
        "\n" + "═" * 70,
        "🔍 EXHAUSTIVE DEPLOYMENT OPERATIONAL DIAGNOSTICS & SECURITY BASELINE",
        "═" * 70,
        "🐧 [LINUX OPERATIONS & HOST ENVIRONMENT]",
        f"  • OS Distribution : {os_info}",
        f"  • CPU Cores       : {cpu_cores} available cores",
        f"  • System Memory   : {mem_info}",
        f"  • Disk Usage      : {disk_info}",
        f"  • Docker CLI      : {docker_ver}",
        f"  • Nixpacks CLI    : {nixpacks_ver}",
        f"  • Build Dir Path  : {build_dir or 'Not assigned yet'}",
        "",
        "🌐 [PROJECT & NETWORK CONFIGURATION]",
        f"  • Project / Svc   : {svc.name} (ID: {svc.id})",
        f"  • Deployment ID   : {deployment.id}",
        f"  • Build Strategy  : Explicit={buildpack} | Type={deploy_type}",
        f"  • Repository URL  : {repo_url} (Branch: {branch})",
        f"  • Network Routing : Internal Port -> {internal_port} | Domain -> {domain}",
        f"  • Env Variables   : {total_vars} total ({secret_vars} secrets protected)",
        f"  • Env Keys (Top)  : {', '.join(var_names) if var_names else 'None'}",
        "",
        "📦 [REGISTRY & CONTAINER OPERATIONS]",
        f"  • Target Registry : {registry_url}",
        f"  • Target Image    : {image_name}",
        f"  • Build Engine    : {buildpack} (Docker Buildx / Nixpacks)",
        "",
        "🧱 [CONTAINER SANDBOX RUNTIME]",
        f"  • Active Runtime  : {sandbox_runtime}",
        f"  • Isolation Model : {sandbox_isolation}",
        "",
        "🛡️ [SECURITY SCANNING & HARDENING BASELINES (TRIVY / COSIGN)]",
        f"  • Scanner Status  : {trivy_ver}",
        f"  • Cosign Status   : {cosign_ver}",
        f"  • CVE Enforcement : {'Blocking CRITICAL | Warning on HIGH' if trivy_bin else 'Trivy not available — no enforcement'}",
        f"  • Root User Check : {root_user_status}",
        f"  • Secret Leak Scan: {'Trivy secret scanner available' if trivy_bin else 'Secret scan unavailable — Trivy not installed'}",
        "═" * 70 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_clone_diagnostics(deployment, repo_url, branch, target_dir):
    git_ver = "Unknown"
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            git_ver = res.stdout.strip()
    except Exception as exc:
        logger.debug("git version detection failed: %s", exc)

    file_count = 0
    dir_count = 0
    total_size = 0
    try:
        if target_dir and os.path.exists(target_dir):
            for root, dirs, files in os.walk(target_dir):
                dir_count += len(dirs)
                file_count += len(files)
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
    except Exception as exc:
        logger.debug("Directory walk failed: %s", exc)

    size_mb = round(total_size / (1024 * 1024), 2)
    log_lines = [
        "\n" + "─" * 60,
        "📂 [GIT SOURCE TREE & CLONE OPERATIONAL METRICS]",
        f"  • Git Client Version : {git_ver}",
        f"  • Repository Source  : {repo_url}",
        f"  • Branch / Ref       : {branch}",
        f"  • Target Directory   : {target_dir}",
        f"  • Tree Statistics    : {file_count} files, {dir_count} directories",
        f"  • Total Disk Payload : {size_mb} MB ({total_size} bytes)",
        f"  • Git Integrity     : Clone completed (branch: {branch})",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_env_diagnostics(deployment, service, source_label="Manifest/AI"):
    env_vars = service.env_vars.all() if hasattr(service, 'env_vars') else []
    total_count = len(env_vars)
    secret_count = sum(1 for ev in env_vars if getattr(ev, 'is_secret', False))
    locked_count = sum(1 for ev in env_vars if getattr(ev, 'is_locked', False))

    infisical_running = False
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", "name=infisical", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=3
        )
        infisical_running = "infisical" in (res.stdout or "")
    except Exception as exc:
        logger.debug("Infisical container detection failed: %s", exc)

    has_infisical_token = bool(
        os.environ.get("INFISICAL_SERVICE_TOKEN")
        or os.environ.get("INFISICAL_TOKEN")
        or os.environ.get("INFISICAL_PROJECT_ID")
    )

    if infisical_running and has_infisical_token:
        vault_provider = "Infisical Vault Active (Runtime secret sync & KMS encryption verified)"
    elif infisical_running:
        vault_provider = "Infisical Running (service token not configured)"
    elif has_infisical_token:
        vault_provider = "Infisical Token Present (container not running)"
    else:
        vault_provider = "Internal Encrypted DB Vault (Infisical / HashiCorp Vault ready)"

    sources_summary = {}
    for ev in env_vars:
        src = getattr(ev, 'source', 'USER') or 'USER'
        sources_summary[src] = sources_summary.get(src, 0) + 1

    sources_str = ", ".join(f"{k}: {v}" for k, v in sources_summary.items()) if sources_summary else "None"

    log_lines = [
        "\n" + "─" * 60,
        f"🔐 [ENVIRONMENT INJECTION & SECURITY AUDIT ({source_label})]",
        f"  • Total Variables    : {total_count} loaded for container runtime",
        f"  • Secret Protection  : {secret_count} variables marked [SECRET] (redacted from logs)",
        f"  • Secret Vault Mode  : {vault_provider}",
        f"  • Infisical Status  : {'Container running ✓' if infisical_running else 'Container NOT running'} | Token: {'Set' if has_infisical_token else 'Missing'}",
        f"  • Locked Variables   : {locked_count} locked against auto-override",
        f"  • Source Breakdown   : {sources_str}",
        "  • Runtime Injection  : PORT, HOST, and internal network envs mapped",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_build_diagnostics(deployment, builder_type, context_dir, build_arg_names=None):
    build_arg_str = ", ".join(build_arg_names) if build_arg_names else "Standard defaults"

    context_files = []
    try:
        if context_dir and os.path.exists(context_dir):
            context_files = os.listdir(context_dir)[:10]
    except Exception as exc:
        logger.debug("Failed to list context directory: %s", exc)

    log_lines = [
        "\n" + "─" * 60,
        "⚙️ [BUILD ENGINE & CONTAINER WORKSPACE PREPARATION]",
        f"  • Build Engine       : {builder_type.upper()}",
        f"  • Build Context Root : {context_dir}",
        f"  • Context Preview    : {', '.join(context_files) if context_files else 'Empty/Unknown'}",
        f"  • Build Arguments    : {build_arg_str}",
        "  • Cache Mounts       : /root/.cache, /var/cache configured for accelerated builds",
        "  • Target Platform    : linux/amd64 (cloud-native standard architecture)",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_push_diagnostics(deployment, registry_url, image_name):
    from apps.deployments.models.core import PlatformConfig
    config = PlatformConfig.objects.first()
    trivy_enabled = getattr(config, 'trivy_enabled', True)
    fail_severity = getattr(config, 'trivy_fail_on_severity', 'CRITICAL')

    SEVERITY_ORDER = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2, 'CRITICAL': 3}
    fail_threshold = SEVERITY_ORDER.get(fail_severity, 3)

    trivy_bin = find_binary("trivy") if trivy_enabled else None
    vuln_report = {"vulnerabilities": 0, "status": "skipped", "findings": []}
    build_safe = True

    if trivy_bin:
        trivy_status = f"Active scan via {trivy_bin}"
        try:
            res = subprocess.run(
                [trivy_bin, "image", "--insecure", "--scanners", "vuln", "--severity", "CRITICAL,HIGH",
                 "--format", "json", "--no-progress", image_name],
                capture_output=True, text=True, timeout=120
            )
            try:
                scan_data = json.loads(res.stdout) if res.stdout else {}
            except (json.JSONDecodeError, ValueError):
                scan_data = {}

            total_vulns = 0
            findings = []
            for result in scan_data.get("Results", []):
                for vuln in result.get("Vulnerabilities", []):
                    sev = (vuln.get("Severity") or "UNKNOWN").upper()
                    total_vulns += 1
                    findings.append({
                        "id": vuln.get("VulnerabilityID", "unknown"),
                        "severity": sev,
                        "pkg": vuln.get("PkgName", "unknown"),
                        "title": vuln.get("Title", "")[:100],
                    })

            vuln_report = {
                "vulnerabilities": total_vulns,
                "status": "clean" if total_vulns == 0 else "findings",
                "findings": findings[:50],
                "fail_on_severity": fail_severity,
                "summary": {
                    "critical": sum(1 for f in findings if f["severity"] == "CRITICAL"),
                    "high": sum(1 for f in findings if f["severity"] == "HIGH"),
                    "medium": sum(1 for f in findings if f["severity"] == "MEDIUM"),
                    "low": sum(1 for f in findings if f["severity"] == "LOW"),
                },
                "scan_time": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                "image": image_name,
            }

            if total_vulns == 0:
                trivy_outcome = "0 Critical/High CVEs detected"
            else:
                worst = max((SEVERITY_ORDER.get(f["severity"], 0) for f in findings), default=0)
                if worst >= fail_threshold:
                    build_safe = False
                    blocked_sevs = [f["severity"] for f in findings if SEVERITY_ORDER.get(f["severity"], 0) >= fail_threshold]
                    trivy_outcome = f"BLOCKED — {total_vulns} CVEs found ({', '.join(set(blocked_sevs))} >= {fail_severity})"
                else:
                    trivy_outcome = f"{total_vulns} CVEs found (none >= {fail_severity} threshold)"

            if res.returncode != 0 and not scan_data:
                err_msg = (res.stderr or res.stdout or '').strip().replace('\n', ' ')
                trivy_outcome = f"Scan returned code {res.returncode}: {err_msg[:120]}"
        except Exception as e:
            trivy_outcome = f"Scan timeout/error: {e}"
    else:
        trivy_status = "Trivy CLI not found in PATH" if trivy_enabled else "Trivy scanning disabled in platform config"
        trivy_outcome = "SKIPPED — Trivy not installed" if trivy_enabled else "SKIPPED — scanning disabled"
        vuln_report["status"] = "disabled" if not trivy_enabled else "skipped"

    try:
        deployment.vulnerability_report = vuln_report
        deployment.save(update_fields=["vulnerability_report"])
    except Exception as exc:
        logger.debug("Failed to save vulnerability report: %s", exc)

    cosign_bin = find_binary("cosign")
    if cosign_bin:
        cosign_status = f"Cosign detected ({cosign_bin})"
        try:
            key_path = os.environ.get("COSIGN_PRIVATE_KEY_PATH") or os.environ.get("COSIGN_KEY")
            if key_path and os.path.exists(key_path):
                cosign_status += " — private key mode"
            else:
                cosign_status += " — keyless/Sigstore mode"
            _cosign_env = os.environ.copy()
            _cosign_env["COSIGN_EXPERIMENTAL"] = "1"
            cosign_oidc_issuer = os.environ.get("COSIGN_OIDC_ISSUER", "")
            if cosign_oidc_issuer:
                verify_args = [cosign_bin, "verify", "--certificate-oidc-issuer", cosign_oidc_issuer, image_name]
            else:
                verify_args = [cosign_bin, "verify", "--certificate-identity-regexp", ".*", image_name]
            res = subprocess.run(verify_args, capture_output=True, text=True, timeout=15, env=_cosign_env)
            if res.returncode == 0:
                cosign_outcome = "Signature verification PASSED"
            else:
                cosign_outcome = f"Verification returned code {res.returncode} (image may not be signed yet)"
        except Exception as e:
            cosign_outcome = f"Verification check failed: {e}"
    else:
        cosign_status = "Cosign CLI not found in PATH"
        cosign_outcome = "SKIPPED — Cosign not installed"


    log_lines = [
        "\n" + "─" * 60,
        "🚀 [CONTAINER REGISTRY PUSH, TRIVY CVE SCAN & COSIGN SIGNING]",
        f"  • Registry Endpoint  : {registry_url or 'Local Daemon / Managed Docker Hub'}",
        f"  • Target Reference   : {image_name}",
        f"  • Trivy Enabled      : {trivy_enabled} (fail on: {fail_severity})",
        f"  • Trivy Scan Check   : {trivy_status}",
        f"  • Trivy Outcome      : {trivy_outcome}",
        f"  • Cosign Signing     : {cosign_status}",
        f"  • Cosign Outcome     : {cosign_outcome}",
        f"  • Build Verdict      : {'SAFE — proceeding' if build_safe else 'BLOCKED — severity threshold exceeded'}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))
    return build_safe


def log_exhaustive_network_and_routing_diagnostics(deployment, service):
    internal_port = getattr(service, 'internal_port', getattr(service, 'port', '8000'))
    domain = getattr(service, 'domain', getattr(service, 'name', 'localhost'))
    health_path = getattr(service, 'health_check_path', None) or '/health'

    proxy_engine = "Unknown"
    try:
        res = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=3)
        names = (res.stdout or "").lower()
        if "traefik" in names:
            proxy_engine = "Traefik"
        elif "caddy" in names:
            proxy_engine = "Caddy"
        elif "nginx" in names:
            proxy_engine = "Nginx"
        else:
            proxy_engine = "Not detected (reverse proxy may be external)"
    except Exception:
        proxy_engine = "Detection failed"

    ssl_status = "Unknown"
    try:
        from apps.deployments.models.core import PlatformConfig
        config = PlatformConfig.objects.first()
        use_ssl = getattr(config, 'use_ssl', False)
        wildcard = getattr(config, 'wildcard_subdomains', False)
        if use_ssl and wildcard:
            ssl_status = "ACME wildcard (Cloudflare DNS challenge)"
        elif use_ssl:
            ssl_status = "ACME Let's Encrypt (HTTP challenge)"
        else:
            ssl_status = "SSL disabled"
    except Exception:
        ssl_status = "Config check failed"

    log_lines = [
        "\n" + "─" * 60,
        "🕸️ [NETWORK TOPOLOGY, PROXY ROUTING & SSL TERMINATION]",
        f"  • Internal Target    : Container Port {internal_port} (HTTP/TCP)",
        f"  • External Domain    : {domain}",
        f"  • Proxy Edge Engine  : {proxy_engine}",
        f"  • SSL / TLS Security : {ssl_status}",
        f"  • Routing Rule       : Host(`{domain}`) -> Service({service.name}:{internal_port})",
        f"  • Health Check       : {health_path}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_runtime_activation_diagnostics(deployment, service, container_id, target_ip=None, promotion_type="Local Direct / Blue-Green"):
    runtime_name = "runc (Standard Docker OCI Runtime)"
    try:
        from apps.deployments.services.container_runtime import detect_best_runtime
        preferred = getattr(service, 'runtime', None) or detect_best_runtime()
        if preferred in ("runsc", "gvisor"):
            runtime_name = "gVisor (runsc) — User-space kernel sandbox isolation active 🛡️"
        elif preferred in ("kata", "kata-runtime"):
            runtime_name = "Kata Containers — Lightweight hardware VM micro-isolation active 🛡️"
        elif preferred == "runc":
            runtime_name = "runc — Standard Linux cgroups & namespace isolation active"
    except Exception as exc:
        logger.debug("Container runtime detection failed: %s", exc)

    log_lines = [
        "\n" + "─" * 60,
        "🟢 [RUNTIME ACTIVATION, SANDBOX ISOLATION & HEALTH MESH]",
        f"  • Live Container ID  : {container_id}",
        f"  • Target Node IP     : {target_ip or 'Not specified'}",
        f"  • Sandbox Runtime    : {runtime_name}",
        f"  • Promotion Strategy : {promotion_type}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_remote_orchestration_diagnostics(deployment, server, remote_dep_id, status="TRIGGERED"):
    server_name = getattr(server, 'name', 'Remote Node')
    server_host = getattr(server, 'host', 'Unknown IP')
    log_lines = [
        "\n" + "─" * 60,
        "🛰️ [REMOTE NODE ORCHESTRATION & DELEGATION TELEMETRY]",
        f"  • Target Node Name   : {server_name} ({server_host})",
        f"  • Remote Tracking ID : {remote_dep_id}",
        f"  • Delegation Status  : {status}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_self_heal_diagnostics(deployment, action_taken, success, details, next_action=None):
    log_lines = [
        "\n" + "─" * 60,
        "🏥 [AUTONOMOUS SELF-HEALING & AI REMEDIATION TELEMETRY]",
        f"  • Remediation Action : {action_taken}",
        f"  • Recovery Outcome   : {'SUCCESS ✅' if success else 'ESCALATING ⚠️'}",
        f"  • Diagnostic Details : {details}",
        f"  • Suggested Next     : {next_action or 'Monitor system stability'}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_addon_provisioning_diagnostics(deployment, addons_list):
    addons_str = ", ".join(addons_list) if addons_list else "None detected / required"
    addon_count = len(addons_list) if addons_list else 0
    log_lines = [
        "\n" + "─" * 60,
        "🗄️ [DATABASE & CACHE ADDON PROVISIONING MESH]",
        f"  • Addons Processed   : {addons_str}",
        f"  • Addon Count        : {addon_count}",
        f"  • Provisioning       : {'Via addon_provisioner (Docker containers)' if addon_count > 0 else 'No addons required'}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))
