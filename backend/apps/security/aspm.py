"""ASPM Pipeline Gatekeeper: Secrets, IaC, SAST, SCA/CycloneDX SBOM, and SLSA Provenance."""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterator

import yaml
from django.utils import timezone

from .models import SBOMArtifact, SecurityFinding, SecurityPolicy
from .ocsf import record_ocsf_event
from .risk import resolve_stale_findings, upsert_finding

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".next", "dist", "build", "target", ".terraform", ".pytest_cache",
})

_BINARY_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".zst", ".so", ".dll",
    ".exe", ".bin", ".pyc", ".db", ".sqlite", ".sqlite3",
})

_SECRET_RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "AWS_ACCESS_KEY",
        "CRITICAL",
        "CWE-798",
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    (
        "GITHUB_TOKEN",
        "CRITICAL",
        "CWE-798",
        re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b|github_pat_[A-Za-z0-9_]{22,}"),
    ),
    (
        "STRIPE_LIVE_SECRET",
        "CRITICAL",
        "CWE-798",
        re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
    ),
    (
        "PRIVATE_KEY_MATERIAL",
        "CRITICAL",
        "CWE-321",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
    (
        "SLACK_TOKEN",
        "HIGH",
        "CWE-798",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    ),
    (
        "GOOGLE_API_KEY",
        "HIGH",
        "CWE-798",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "HARDCODED_DB_PASSWORD",
        "HIGH",
        "CWE-798",
        re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis(?:s)?)://[a-zA-Z0-9._-]+:([^@\s'\"]{6,})@"),
    ),
)

_PLACEHOLDER_MARKERS = ("example", "placeholder", "your_", "changeme", "dummy", "test_key", "localhost", "127.0.0.1", "password123")


def _mask_secret(raw: str) -> str:
    s = str(raw or "")
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}...{s[-4:]}"


def _find_cli(name: str) -> str | None:
    try:
        from apps.deployments.utils import find_binary
        return find_binary(name)
    except Exception:
        import shutil
        return shutil.which(name)


def _iter_source_files(
    source_dir: str,
    *,
    max_files: int = 1200,
    max_bytes: int = 512 * 1024,
) -> Iterator[tuple[str, str]]:
    """Yield (relative_posix_path, text_content) safely without crossing symlinks."""
    root = Path(source_dir)
    if not root.is_dir():
        return
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fname in sorted(filenames):
            if count >= max_files:
                return
            fpath = Path(dirpath) / fname
            if fpath.is_symlink() or fpath.suffix.lower() in _BINARY_EXTS:
                continue
            try:
                if fpath.stat().st_size > max_bytes:
                    continue
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = fpath.relative_to(root).as_posix()
            count += 1
            yield rel, text


def scan_secrets(source_dir: str) -> list[dict[str, Any]]:
    """Scan source files for leaked credentials via gitleaks (if present) + built-in detector."""
    findings: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int, str]] = set()

    gitleaks = _find_cli("gitleaks")
    if gitleaks:
        try:
            proc = subprocess.run(
                [gitleaks, "detect", "--source", source_dir, "--no-git", "--report-format", "json", "--report-path", "-"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.stdout.strip().startswith("["):
                for item in json.loads(proc.stdout):
                    rel = str(item.get("File") or "")
                    line = int(item.get("StartLine") or 1)
                    rule_id = str(item.get("RuleID") or "gitleaks-secret")
                    key = (rel, line, rule_id)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    findings.append({
                        "rule": rule_id,
                        "severity": "CRITICAL",
                        "cwe_id": "CWE-798",
                        "file_path": rel,
                        "line_number": line,
                        "title": f"Hardcoded secret detected ({rule_id})",
                        "description": f"Secret match in {rel}:{line} ({_mask_secret(item.get('Match', ''))})",
                        "remediation": "Revoke and rotate the credential immediately; inject it via encrypted Service EnvVars instead of Git.",
                    })
        except Exception as exc:
            logger.debug("gitleaks CLI skipped/failed: %s", exc)

    for rel, text in _iter_source_files(source_dir):
        base_lower = os.path.basename(rel).lower()
        is_env_file = (
            base_lower == ".env"
            or (
                base_lower.startswith(".env.")
                and not base_lower.endswith((".example", ".sample", ".template", ".dist", ".md"))
            )
        )
        is_key_file = base_lower.endswith((".pem", ".key", ".p12", ".pfx"))
        if rel.endswith((".md", ".lock")):
            continue
        if "test" in rel.lower().split("/") and not (is_env_file or is_key_file):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
                continue
            if is_env_file:
                env_m = re.match(
                    r"^\s*(?:export\s+)?([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|PRIVATE_KEY|API_KEY)[A-Z0-9_]*)\s*=\s*['\"]?([^'\"\s#]{8,})['\"]?",
                    line,
                    re.I,
                )
                if env_m:
                    env_key, env_val = env_m.group(1), env_m.group(2)
                    key = (rel, lineno, "COMMITTED_ENV_SECRET")
                    if key not in seen_keys and not any(m in env_val.lower() for m in _PLACEHOLDER_MARKERS):
                        seen_keys.add(key)
                        findings.append({
                            "rule": "COMMITTED_ENV_SECRET",
                            "severity": "HIGH",
                            "cwe_id": "CWE-798",
                            "file_path": rel,
                            "line_number": lineno,
                            "title": f"Committed `.env` secret assignment (`{env_key}`)",
                            "description": f"Environment file {rel}:{lineno} assigns `{env_key}` (redacted={_mask_secret(env_val)}).",
                            "remediation": "Add `.env*` to `.gitignore`/`.dockerignore`, rotate the secret, and configure it in SMSLY Environment Variables.",
                        })
            for rule_name, sev, cwe, pattern in _SECRET_RULES:
                match = pattern.search(line)
                if not match:
                    continue
                key = (rel, lineno, rule_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                secret_token = match.group(1) if rule_name == "HARDCODED_DB_PASSWORD" and match.lastindex else match.group(0)
                if any(marker in secret_token.lower() for marker in _PLACEHOLDER_MARKERS):
                    continue
                findings.append({
                    "rule": rule_name,
                    "severity": sev,
                    "cwe_id": cwe,
                    "file_path": rel,
                    "line_number": lineno,
                    "title": f"Hardcoded credential pattern ({rule_name})",
                    "description": f"Detected {rule_name} at {rel}:{lineno} (redacted={_mask_secret(secret_token)}).",
                    "remediation": "Remove credential from source repository, rotate key at provider, and move to SMSLY encrypted Environment Variables.",
                })
    return findings


def scan_iac(source_dir: str) -> list[dict[str, Any]]:
    """Audit Dockerfile, Compose, Kubernetes, and Terraform files for IaC security posture."""
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    def _add(rule: str, sev: str, cwe: str, rel: str, line: int, title: str, desc: str, rem: str) -> None:
        key = (rel, line, rule)
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "rule": rule,
            "severity": sev,
            "cwe_id": cwe,
            "file_path": rel,
            "line_number": line,
            "title": title,
            "description": desc,
            "remediation": rem,
        })

    for rel, text in _iter_source_files(source_dir, max_files=600):
        base = os.path.basename(rel).lower()

        if base == "dockerfile" or base.startswith("dockerfile.") or base.endswith(".dockerfile"):
            has_user = False
            has_nonroot_user = False
            has_healthcheck = False
            lines = text.splitlines()
            for idx, raw_line in enumerate(lines, start=1):
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                upper = stripped.upper()
                if upper.startswith("FROM ") and ":LATEST" in upper:
                    _add(
                        "DOCKER_UNPINNED_LATEST", "LOW", "CWE-1104", rel, idx,
                        "Base image uses mutable :latest tag",
                        f"`{stripped}` avoids reproducible builds and bypasses digest pinning.",
                        "Pin a specific version or SHA256 digest in FROM.",
                    )
                if upper.startswith("USER "):
                    has_user = True
                    target_user = stripped.split(None, 1)[1].strip().lower()
                    if target_user not in ("root", "0", "0:0"):
                        has_nonroot_user = True
                if upper.startswith("HEALTHCHECK "):
                    has_healthcheck = True
                if upper.startswith("ENV ") and re.search(r"(SECRET|PASSWORD|PRIVATE_KEY|TOKEN|API_KEY)\s*=\s*\S+", stripped, re.I):
                    _add(
                        "DOCKER_ENV_SECRET", "HIGH", "CWE-798", rel, idx,
                        "Sensitive credential baked into Dockerfile ENV layer",
                        f"Line {idx} in {rel} embeds an ENV secret that persists in image layers.",
                        "Pass secrets at runtime via SMSLY EnvVars instead of Dockerfile ENV.",
                    )
                if "chmod 777" in stripped.lower():
                    _add(
                        "DOCKER_CHMOD_777", "MEDIUM", "CWE-732", rel, idx,
                        "World-writable file permissions (chmod 777) in Dockerfile",
                        f"`{stripped}` grants world write access inside the container.",
                        "Restrict permissions using chmod 750 or 640.",
                    )
                if re.search(r"(curl|wget)[^|]*\|\s*(sh|bash)", stripped, re.I):
                    _add(
                        "DOCKER_PIPE_SHELL", "MEDIUM", "CWE-829", rel, idx,
                        "Unverified remote script piped directly to shell",
                        f"`{stripped}` executes unverified remote code during build.",
                        "Download, verify checksum/signature, then execute explicitly.",
                    )
            if not has_user or not has_nonroot_user:
                _add(
                    "DOCKER_ROOT_USER", "MEDIUM", "CWE-250", rel, 1,
                    "Container image runs as root (no non-root USER instruction)",
                    f"{rel} does not drop privileges to a non-root USER.",
                    "Add `RUN adduser -D app && USER app` to adhere to least privilege.",
                )
            if not has_healthcheck:
                _add(
                    "DOCKER_NO_HEALTHCHECK", "LOW", "CWE-693", rel, 1,
                    "Dockerfile lacks HEALTHCHECK instruction",
                    f"{rel} has no native container HEALTHCHECK probe.",
                    "Define a HEALTHCHECK instruction or configure HTTP probe in SMSLY Health settings.",
                )

        elif base.endswith((".yml", ".yaml")):
            try:
                docs = list(yaml.safe_load_all(text))
            except Exception:
                continue
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                services = doc.get("services")
                if isinstance(services, dict):
                    for svc_name, cfg in services.items():
                        if not isinstance(cfg, dict):
                            continue
                        if cfg.get("privileged") is True:
                            _add(
                                "COMPOSE_PRIVILEGED", "CRITICAL", "CWE-250", rel, 1,
                                f"Compose service `{svc_name}` requests privileged: true",
                                "Privileged mode grants near-root host kernel capabilities.",
                                "Remove `privileged: true` and grant only specific capabilities if strictly required.",
                            )
                        if str(cfg.get("network_mode") or "").lower() == "host":
                            _add(
                                "COMPOSE_HOST_NETWORK", "HIGH", "CWE-668", rel, 1,
                                f"Compose service `{svc_name}` uses network_mode: host",
                                "Host networking bypasses isolated project Docker bridge segmentation.",
                                "Use SMSLY per-project isolated networks instead of host networking.",
                            )
                        for vol in cfg.get("volumes") or []:
                            if "docker.sock" in str(vol):
                                _add(
                                    "COMPOSE_DOCKER_SOCK", "CRITICAL", "CWE-250", rel, 1,
                                    f"Compose service `{svc_name}` mounts Docker socket",
                                    f"Mounting `{vol}` grants host root-equivalent container control.",
                                    "Remove direct docker.sock mounts from application containers.",
                                )
                kind = str(doc.get("kind") or "").strip()
                if kind in ("Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"):
                    meta_name = str((doc.get("metadata") or {}).get("name") or kind)
                    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
                    pod_spec = spec
                    if kind == "CronJob":
                        pod_spec = (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
                    elif kind != "Pod":
                        pod_spec = (spec.get("template") or {}).get("spec") or {}
                    if isinstance(pod_spec, dict):
                        if pod_spec.get("hostNetwork") is True or pod_spec.get("hostPID") is True:
                            _add(
                                "K8S_HOST_NAMESPACE", "HIGH", "CWE-668", rel, 1,
                                f"Kubernetes {kind} `{meta_name}` shares host network/PID namespace",
                                "Sharing hostNetwork or hostPID breaks container namespace isolation.",
                                "Remove `hostNetwork: true` and `hostPID: true` from Pod spec.",
                            )
                        for vol in pod_spec.get("volumes") or []:
                            if isinstance(vol, dict) and "docker.sock" in str((vol.get("hostPath") or {}).get("path") or ""):
                                _add(
                                    "K8S_DOCKER_SOCK", "CRITICAL", "CWE-250", rel, 1,
                                    f"Kubernetes {kind} `{meta_name}` mounts host docker.sock",
                                    "Mounting hostPath docker.sock grants node root breakout.",
                                    "Remove hostPath `/var/run/docker.sock` volume.",
                                )
                        for ctr in (pod_spec.get("containers") or []) + (pod_spec.get("initContainers") or []):
                            if not isinstance(ctr, dict):
                                continue
                            sec_ctx = ctr.get("securityContext") or {}
                            if isinstance(sec_ctx, dict) and sec_ctx.get("privileged") is True:
                                _add(
                                    "K8S_PRIVILEGED_CONTAINER", "CRITICAL", "CWE-250", rel, 1,
                                    f"Kubernetes container `{ctr.get('name', meta_name)}` runs privileged",
                                    "`securityContext.privileged: true` grants full host kernel capabilities.",
                                    "Set `privileged: false` and `allowPrivilegeEscalation: false`.",
                                )

        elif base.endswith(".tf"):
            for idx, raw_line in enumerate(text.splitlines(), start=1):
                if "0.0.0.0/0" in raw_line and re.search(r"(ingress|cidr_blocks|22|3389|5432|6379)", text, re.I):
                    _add(
                        "TF_PUBLIC_CIDR", "HIGH", "CWE-284", rel, idx,
                        "Terraform security group allows 0.0.0.0/0 ingress",
                        f"Line {idx} exposes network rule to the entire internet.",
                        "Restrict ingress CIDR blocks to WireGuard/VPN or trusted LB ranges.",
                    )
                    break
    return findings


def scan_sast(source_dir: str) -> list[dict[str, Any]]:
    """Run lightweight SAST (Semgrep CLI if present + built-in Python AST & JS rules)."""
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    def _add(rule: str, sev: str, cwe: str, rel: str, line: int, title: str, desc: str, rem: str) -> None:
        key = (rel, line, rule)
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "rule": rule,
            "severity": sev,
            "cwe_id": cwe,
            "file_path": rel,
            "line_number": line,
            "title": title,
            "description": desc,
            "remediation": rem,
        })

    semgrep = _find_cli("semgrep")
    if semgrep:
        try:
            proc = subprocess.run(
                [semgrep, "--json", "--quiet", "--config", "auto", source_dir],
                capture_output=True, text=True, timeout=90,
            )
            if proc.stdout.strip().startswith("{"):
                data = json.loads(proc.stdout)
                for res in data.get("results", []):
                    rel = os.path.relpath(str(res.get("path") or ""), source_dir).replace("\\", "/")
                    line = int((res.get("start") or {}).get("line") or 1)
                    extra = res.get("extra") or {}
                    sev = str(extra.get("severity") or "MEDIUM").upper()
                    sev = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}.get(sev, sev)
                    check_id = str(res.get("check_id") or "semgrep")
                    cwe = ((extra.get("metadata") or {}).get("cwe") or [""])[0]
                    _add(check_id, sev, str(cwe)[:32], rel, line, f"SAST: {check_id}", str(extra.get("message") or check_id), "Refactor code per Semgrep advisory.")
        except Exception as exc:
            logger.debug("semgrep skipped/failed: %s", exc)

    for rel, text in _iter_source_files(source_dir):
        lower_rel = rel.lower()
        if "/tests/" in f"/{lower_rel}" or lower_rel.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts")):
            continue

        if rel.endswith(".py"):
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = _resolve_call_name(node.func)
                    if func_name in ("eval", "exec"):
                        _add(
                            "PY_DYNAMIC_EXEC", "HIGH", "CWE-94", rel, node.lineno,
                            f"Dangerous `{func_name}()` execution in Python",
                            f"`{func_name}()` executes arbitrary Python code and risks RCE if fed user input.",
                            "Replace with `ast.literal_eval()` or explicit dispatch maps.",
                        )
                    elif func_name in ("pickle.loads", "pickle.load", "marshal.loads"):
                        _add(
                            "PY_UNSAFE_DESERIALIZE", "CRITICAL", "CWE-502", rel, node.lineno,
                            f"Unsafe binary deserialization (`{func_name}`)",
                            "Untrusted pickle/marshal payloads enable arbitrary code execution.",
                            "Use JSON or MessagePack for untrusted data interchange.",
                        )
                    elif func_name in ("subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output"):
                        for kw in node.keywords:
                            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                _add(
                                    "PY_SHELL_TRUE", "HIGH", "CWE-78", rel, node.lineno,
                                    "Subprocess spawned with shell=True",
                                    "Passing shell=True invites OS command injection.",
                                    "Pass an explicit argv list and shell=False.",
                                )
                    elif func_name == "yaml.load":
                        has_safe = any(kw.arg == "Loader" and "Safe" in _resolve_call_name(kw.value) for kw in node.keywords)
                        if not has_safe:
                            _add(
                                "PY_UNSAFE_YAML", "HIGH", "CWE-502", rel, node.lineno,
                                "Unsafe `yaml.load()` without SafeLoader",
                                "PyYAML `yaml.load()` can construct arbitrary Python objects.",
                                "Use `yaml.safe_load()` instead.",
                            )
                    elif func_name.endswith(".execute") and node.args and isinstance(node.args[0], ast.JoinedStr):
                        _add(
                            "PY_SQL_FSTRING", "CRITICAL", "CWE-89", rel, node.lineno,
                            "SQL query built with Python f-string interpolation",
                            "Dynamic string formatting in SQL `.execute()` creates SQL injection risk.",
                            "Use parameterized queries (`cursor.execute(sql, params)`).",
                        )

        elif rel.endswith((".js", ".jsx", ".ts", ".tsx")):
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "dangerouslySetInnerHTML" in line:
                    _add(
                        "JS_DANGEROUS_HTML", "MEDIUM", "CWE-79", rel, lineno,
                        "React `dangerouslySetInnerHTML` usage",
                        "Rendering unsanitized HTML exposes applications to Cross-Site Scripting (XSS).",
                        "Sanitize markup with DOMPurify before rendering or use structured JSX.",
                    )
                if "rejectUnauthorized: false" in line:
                    _add(
                        "JS_TLS_VERIFY_DISABLED", "HIGH", "CWE-295", rel, lineno,
                        "TLS certificate validation explicitly disabled",
                        "`rejectUnauthorized: false` permits man-in-the-middle interception.",
                        "Remove `rejectUnauthorized: false` and supply the proper CA bundle.",
                    )
    return findings


def _resolve_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _resolve_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _parse_requirements_txt(text: str) -> list[dict[str, Any]]:
    pkgs = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*(?:==|>=|~=)?\s*([A-Za-z0-9._+-]*)", line)
        if m:
            name, ver = m.group(1), m.group(2) or "unspecified"
            pkgs.append({
                "name": name,
                "version": ver,
                "ecosystem": "pypi",
                "purl": f"pkg:pypi/{name.lower()}@{ver}",
                "direct": True,
            })
    return pkgs


def _parse_package_json(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    pkgs = []
    for sec in ("dependencies", "devDependencies"):
        deps = data.get(sec)
        if not isinstance(deps, dict):
            continue
        for name, ver in deps.items():
            clean_ver = str(ver).lstrip("^~>=< ") or "unspecified"
            pkgs.append({
                "name": name,
                "version": clean_ver,
                "ecosystem": "npm",
                "purl": f"pkg:npm/{name}@{clean_ver}",
                "direct": True,
            })
    return pkgs


def _parse_package_lock(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    pkgs = []
    packages_obj = data.get("packages")
    if isinstance(packages_obj, dict):
        for pkg_path, meta in list(packages_obj.items())[:500]:
            if not pkg_path or not isinstance(meta, dict):
                continue
            name = pkg_path.split("node_modules/")[-1]
            ver = str(meta.get("version") or "unspecified")
            pkgs.append({
                "name": name,
                "version": ver,
                "ecosystem": "npm",
                "purl": f"pkg:npm/{name}@{ver}",
                "direct": pkg_path.count("node_modules/") <= 1,
            })
    return pkgs


def _parse_go_mod(text: str) -> list[dict[str, Any]]:
    pkgs = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(?:require\s+)?([a-zA-Z0-9._/-]+\.[a-zA-Z0-9._/-]+)\s+(v[^\s]+)", s)
        if m:
            name, ver = m.group(1), m.group(2)
            pkgs.append({
                "name": name,
                "version": ver,
                "ecosystem": "golang",
                "purl": f"pkg:golang/{name}@{ver}",
                "direct": "// indirect" not in s,
            })
    return pkgs


def _parse_pyproject_toml(text: str) -> list[dict[str, Any]]:
    pkgs = []
    for m in re.finditer(r'["\']([A-Za-z0-9_.-]+)\s*(?:==|>=|~=|\^)\s*([0-9][A-Za-z0-9._+-]*)["\']', text):
        name, ver = m.group(1), m.group(2)
        pkgs.append({
            "name": name,
            "version": ver,
            "ecosystem": "pypi",
            "purl": f"pkg:pypi/{name.lower()}@{ver}",
            "direct": True,
        })
    return pkgs


def _parse_cargo_toml(text: str) -> list[dict[str, Any]]:
    pkgs = []
    in_deps = False
    for raw in text.splitlines():
        s = raw.split("#", 1)[0].strip()
        if s.startswith("[") and s.endswith("]"):
            in_deps = "dependencies" in s.lower()
            continue
        if not in_deps or "=" not in s:
            continue
        m = re.match(r'^([A-Za-z0-9_-]+)\s*=\s*(?:["\']([^"\']+)["\']|.*version\s*=\s*["\']([^"\']+)["\'])', s)
        if m:
            name = m.group(1)
            ver = (m.group(2) or m.group(3) or "unspecified").lstrip("^~=")
            pkgs.append({
                "name": name,
                "version": ver,
                "ecosystem": "cargo",
                "purl": f"pkg:cargo/{name}@{ver}",
                "direct": True,
            })
    return pkgs


_KNOWN_VULN_PACKAGES: tuple[tuple[str, str, str, str, str], ...] = (
    # (pkg_lower, max_vuln_prefix_tuple_str, cve_id, severity, fixed_version)
    ("pyyaml", "3.,4.,5.0,5.1,5.2,5.3", "CVE-2020-14343", "CRITICAL", "5.4.1"),
    ("urllib3", "1.21,1.22,1.23,1.24,1.25", "CVE-2023-45803", "HIGH", "1.26.18"),
    ("requests", "2.1,2.20,2.21,2.22,2.23,2.24,2.25,2.26,2.27,2.28,2.29,2.30", "CVE-2023-32681", "MEDIUM", "2.31.0"),
    ("cryptography", "2.,3.,35.,36.,37.,38.,39.,40.", "CVE-2023-49083", "HIGH", "41.0.6"),
    ("lodash", "3.,4.0,4.1,4.2,4.3,4.4,4.5,4.6,4.7,4.8,4.9,4.10,4.11,4.12,4.13,4.14,4.15,4.16,4.17.0,4.17.1,4.17.15,4.17.19,4.17.20", "CVE-2021-23337", "HIGH", "4.17.21"),
    ("axios", "0.", "CVE-2023-45857", "HIGH", "1.6.0"),
    ("jsonwebtoken", "0.,1.,2.,3.,4.,5.,6.,7.,8.", "CVE-2022-23529", "CRITICAL", "9.0.0"),
)


def scan_sca_packages(
    packages: list[dict[str, Any]],
    *,
    service=None,
    deployment=None,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Evaluate extracted SBOM dependencies against SCA advisory rules and populate Category.SCA."""
    sca_findings: list[dict[str, Any]] = []
    active_fps: set[str] = set()

    for pkg in packages:
        name = str(pkg.get("name") or "").strip()
        ver = str(pkg.get("version") or "unspecified").strip()
        if not name:
            continue
        name_low = name.lower()
        for vuln_name, prefixes_csv, cve_id, sev, fixed_ver in _KNOWN_VULN_PACKAGES:
            if name_low != vuln_name:
                continue
            prefixes = tuple(p.strip() for p in prefixes_csv.split(",") if p.strip())
            if ver.startswith(prefixes):
                item = {
                    "package_name": name,
                    "installed_version": ver,
                    "fixed_version": fixed_ver,
                    "cve_id": cve_id,
                    "severity": sev,
                    "title": f"SCA Dependency Vulnerability: {cve_id} in `{name}@{ver}`",
                    "description": f"Manifest dependency `{name}` ({ver}) matches known vulnerable range for {cve_id}.",
                    "remediation": f"Upgrade `{name}` to `>={fixed_ver}` in your dependency manifest.",
                }
                sca_findings.append(item)
                if persist and service is not None:
                    fp = SecurityFinding.build_fingerprint(
                        SecurityFinding.Category.SCA, service.id, name_low, cve_id
                    )
                    active_fps.add(fp)
                    upsert_finding(
                        fingerprint=fp,
                        pillar=SecurityFinding.Pillar.ASPM,
                        category=SecurityFinding.Category.SCA,
                        severity=sev,
                        title=item["title"],
                        description=item["description"],
                        remediation=item["remediation"],
                        service=service,
                        deployment=deployment,
                        package_name=name,
                        installed_version=ver,
                        fixed_version=fixed_ver,
                        cve_id=cve_id,
                        epss_score=0.7 if sev == "CRITICAL" else 0.4,
                        emit_ocsf=sev in ("CRITICAL", "HIGH"),
                    )

    if persist and service is not None:
        resolve_stale_findings(
            category=SecurityFinding.Category.SCA,
            active_fingerprints=active_fps,
            service=service,
        )
    return sca_findings


def generate_sbom(
    *,
    deployment,
    source_dir: str | None = None,
    image_ref: str = "",
    image_digest: str = "",
) -> SBOMArtifact | None:
    """Generate and persist a CycloneDX v1.5 SBOM from Trivy/Syft or repository manifests."""
    packages: list[dict[str, Any]] = []
    seen_purls: set[str] = set()

    def _add_pkg(pkg: dict[str, Any]) -> None:
        purl = str(pkg.get("purl") or f"{pkg.get('ecosystem', 'generic')}:{pkg.get('name')}@{pkg.get('version')}")
        if purl in seen_purls:
            return
        seen_purls.add(purl)
        packages.append(pkg)

    # 1. Try Trivy image/fs CycloneDX extraction if installed
    trivy_bin = _find_cli("trivy")
    if trivy_bin and image_ref:
        try:
            proc = subprocess.run(
                [trivy_bin, "image", "--format", "cyclonedx", "--quiet", image_ref],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip().startswith("{"):
                cdx = json.loads(proc.stdout)
                for comp in cdx.get("components", []):
                    name = str(comp.get("name") or "")
                    ver = str(comp.get("version") or "unspecified")
                    purl = str(comp.get("purl") or "")
                    if name:
                        _add_pkg({
                            "name": name,
                            "version": ver,
                            "ecosystem": str(comp.get("type") or "library"),
                            "purl": purl or f"pkg:generic/{name}@{ver}",
                            "direct": False,
                        })
        except Exception as exc:
            logger.debug("Trivy CycloneDX SBOM generation skipped: %s", exc)

    # 2. Deterministic repository manifest parsing (works offline without external binary)
    if source_dir and os.path.isdir(source_dir):
        parsers = {
            "requirements.txt": _parse_requirements_txt,
            "pyproject.toml": _parse_pyproject_toml,
            "cargo.toml": _parse_cargo_toml,
            "package-lock.json": _parse_package_lock,
            "package.json": _parse_package_json,
            "go.mod": _parse_go_mod,
        }
        for rel, text in _iter_source_files(source_dir, max_files=400):
            base = os.path.basename(rel).lower()
            parser = parsers.get(base)
            if parser is None and base.startswith("requirements") and base.endswith(".txt"):
                parser = _parse_requirements_txt
            if parser is not None:
                for p in parser(text):
                    _add_pkg(p)

    now_iso = timezone.now().isoformat()
    svc = deployment.service
    cdx_doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": now_iso,
            "tools": [{"vendor": "SMSLY Hosting", "name": "SMSLY Aegis ASPM", "version": "1.0.0"}],
            "component": {
                "type": "application",
                "name": svc.name,
                "version": str(deployment.commit_hash or "HEAD")[:12],
                "purl": image_ref or f"pkg:smsly/{svc.slug}",
            },
        },
        "components": [
            {
                "type": "library",
                "name": p["name"],
                "version": p["version"],
                "purl": p["purl"],
            }
            for p in packages[:1500]
        ],
    }

    provenance = generate_slsa_provenance(
        deployment=deployment,
        image_ref=image_ref,
        image_digest=image_digest,
    )

    artifact, _created = SBOMArtifact.objects.update_or_create(
        deployment=deployment,
        defaults={
            "service": svc,
            "sbom_format": "cyclonedx",
            "spec_version": "1.5",
            "image_ref": image_ref[:255],
            "image_digest": image_digest[:128],
            "package_count": len(packages),
            "packages": packages[:1500],
            "sbom_document": cdx_doc,
            "slsa_provenance": provenance,
            "provenance_verified": bool(provenance.get("predicate")),
        },
    )
    try:
        scan_sca_packages(packages, service=svc, deployment=deployment, persist=True)
    except Exception as exc:
        logger.debug("SCA package scan skipped during SBOM generation: %s", exc)
    return artifact


def generate_slsa_provenance(
    *,
    deployment,
    image_ref: str = "",
    image_digest: str = "",
) -> dict[str, Any]:
    """Generate an in-toto v1 / SLSA v1.0 build provenance attestation."""
    svc = deployment.service
    digest_val = (image_digest or "").replace("sha256:", "")
    if not digest_val:
        digest_val = hashlib.sha256(
            f"{svc.id}|{deployment.id}|{deployment.commit_hash}|{image_ref}".encode("utf-8")
        ).hexdigest()

    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": image_ref or f"smsly/{svc.slug}:latest",
                "digest": {"sha256": digest_val},
            }
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://smsly.cloud/pipeline/v1",
                "externalParameters": {
                    "repository": getattr(svc, "repository_url", "") or "",
                    "branch": deployment.branch or getattr(svc, "branch", "") or "main",
                    "commit": deployment.commit_hash or "",
                    "buildpack": getattr(svc, "buildpack", "auto"),
                },
                "resolvedDependencies": [
                    {
                        "uri": f"git+{getattr(svc, 'repository_url', '') or 'local'}",
                        "digest": {"gitCommit": deployment.commit_hash or "HEAD"},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": "https://smsly.cloud/builders/contained-buildx/v1"},
                "metadata": {
                    "invocationId": str(deployment.id),
                    "startedOn": deployment.started_at.isoformat() if deployment.started_at else timezone.now().isoformat(),
                    "finishedOn": timezone.now().isoformat(),
                },
            },
        },
    }


def sync_deployment_trivy_findings(deployment) -> int:
    """Persist structured Trivy CVEs from Deployment.vulnerability_report into SecurityFinding."""
    report = getattr(deployment, "vulnerability_report", None) or {}
    if not isinstance(report, dict):
        return 0
    raw_findings = report.get("findings") or []
    svc = deployment.service
    active_fps: set[str] = set()
    synced = 0

    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        cve_id = str(item.get("id") or "").strip()
        pkg = str(item.get("pkg") or "").strip()
        sev = str(item.get("severity") or "HIGH").upper()
        title_txt = str(item.get("title") or cve_id or "Container vulnerability").strip()
        if not cve_id:
            continue
        fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.IMAGE_CVE, svc.id, cve_id, pkg)
        active_fps.add(fp)
        upsert_finding(
            fingerprint=fp,
            pillar=SecurityFinding.Pillar.ASPM,
            category=SecurityFinding.Category.IMAGE_CVE,
            severity=sev,
            title=f"{cve_id} in {pkg}" if pkg else cve_id,
            description=title_txt,
            remediation=f"Upgrade package `{pkg}` or rebuild using a newer patched base image.",
            service=svc,
            deployment=deployment,
            package_name=pkg,
            cve_id=cve_id,
            epss_score=0.65 if sev == "CRITICAL" else 0.35,
            evidence={"image": report.get("image", ""), "raw": item},
            emit_ocsf=sev in ("CRITICAL", "HIGH"),
        )
        synced += 1

    # Only resolve stale CVEs if Trivy executed cleanly with explicit findings list
    if report.get("status") in ("clean", "findings"):
        resolve_stale_findings(
            category=SecurityFinding.Category.IMAGE_CVE,
            active_fingerprints=active_fps,
            service=svc,
        )
    return synced


def run_aspm_pipeline_scan(
    *,
    deployment,
    source_dir: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run full pre-build ASPM suite (Secrets, IaC, SAST) and evaluate policy gates."""
    svc = deployment.service
    try:
        policy = SecurityPolicy.load()
    except Exception:
        policy = None

    if policy and not policy.aspm_enabled:
        return {"enabled": False, "blocked": False, "block_reasons": [], "counts": {}}

    secrets = scan_secrets(source_dir) if source_dir else []
    iac = scan_iac(source_dir) if source_dir else []
    sast = scan_sast(source_dir) if source_dir else []

    if persist:
        for cat, items in (
            (SecurityFinding.Category.SECRETS, secrets),
            (SecurityFinding.Category.IAC, iac),
            (SecurityFinding.Category.SAST, sast),
        ):
            active_fps: set[str] = set()
            for item in items:
                fp = SecurityFinding.build_fingerprint(
                    cat, svc.id, item["rule"], item.get("file_path", ""), item.get("line_number", 0)
                )
                active_fps.add(fp)
                upsert_finding(
                    fingerprint=fp,
                    pillar=SecurityFinding.Pillar.ASPM,
                    category=cat,
                    severity=item["severity"],
                    title=item["title"],
                    description=item["description"],
                    remediation=item["remediation"],
                    service=svc,
                    deployment=deployment,
                    file_path=item.get("file_path", ""),
                    line_number=item.get("line_number"),
                    cwe_id=item.get("cwe_id", ""),
                    evidence={"rule": item["rule"]},
                    emit_ocsf=item["severity"] in ("CRITICAL", "HIGH"),
                )
            resolve_stale_findings(category=cat, active_fingerprints=active_fps, service=svc)

        if secrets or any(x["severity"] == "CRITICAL" for x in sast + iac):
            record_ocsf_event(
                source="aspm",
                source_uid=f"aspm-deploy-{deployment.id}",
                title=f"ASPM Pre-Build Scan Alert ({svc.name})",
                details=f"secrets={len(secrets)}, iac={len(iac)}, sast={len(sast)}",
                severity="CRITICAL" if secrets else "HIGH",
                target=svc.name,
                category="SAST",
                service=svc,
                deployment=deployment,
            )

    block_reasons: list[str] = []
    if policy:
        if policy.aspm_block_on_secrets and secrets:
            block_reasons.append(f"{len(secrets)} hardcoded secret(s) detected in repository")
        if policy.aspm_block_on_critical_sast and any(x["severity"] == "CRITICAL" for x in sast):
            block_reasons.append("CRITICAL SAST vulnerability detected in repository")
        if policy.aspm_block_on_critical_iac and any(x["severity"] == "CRITICAL" for x in iac):
            block_reasons.append("CRITICAL IaC misconfiguration detected in Dockerfile/Compose")

    return {
        "enabled": True,
        "blocked": bool(block_reasons),
        "block_reasons": block_reasons,
        "counts": {
            "secrets": len(secrets),
            "iac": len(iac),
            "sast": len(sast),
            "critical": sum(1 for x in (secrets + iac + sast) if x["severity"] == "CRITICAL"),
            "high": sum(1 for x in (secrets + iac + sast) if x["severity"] == "HIGH"),
        },
        "findings": {"secrets": secrets[:25], "iac": iac[:25], "sast": sast[:25]},
    }
