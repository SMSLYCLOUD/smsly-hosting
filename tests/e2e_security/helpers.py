"""Shared helpers for signing, Caddyfile/YAML parsing, and WSL bash execution."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_path(*parts: str | os.PathLike[str]) -> Path:
    """Resolve a path relative to the repository root."""
    return REPO_ROOT.joinpath(*parts)


def sign_gateway_v2(
    method: str,
    path: str,
    body_bytes: bytes | str | None = b"",
    timestamp: int | str | None = None,
    nonce: str | None = None,
    secret: str | None = None,
) -> dict[str, str]:
    """Build SecurityMiddleware / Gateway V2 HMAC headers.

    Payload format: ``METHOD|PATH|TIMESTAMP|NONCE|SHA256(BODY)``.
    Note: ``SecurityMiddleware`` hashes ``request.path`` (without query string),
    while ``ZeroTrustHMACAuthentication`` and ``RemoteSyncHMACAuthentication``
    hash ``request.get_full_path()`` (with query string when present).
    """
    from django.conf import settings

    if body_bytes is None:
        raw_body = b""
    elif isinstance(body_bytes, str):
        raw_body = body_bytes.encode("utf-8")
    else:
        raw_body = bytes(body_bytes)

    ts = str(int(time.time()) if timestamp is None else timestamp)
    nonce_val = uuid.uuid4().hex if nonce is None else str(nonce)
    gw_secret = (
        secret
        if secret is not None
        else str(getattr(settings, "GATEWAY_SECRET", "") or getattr(settings, "SECRET_KEY", ""))
    )

    body_hash = hashlib.sha256(raw_body).hexdigest()
    payload = f"{method.upper()}|{path}|{ts}|{nonce_val}|{body_hash}"
    sig = hmac.new(
        gw_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return {
        "X-Gateway-Signature-V2": sig,
        "X-Request-Timestamp": ts,
        "X-Request-Nonce": nonce_val,
    }


def sign_election_v2(
    sender_wg: str,
    body_dict: dict[str, Any],
    secret: str,
    timestamp: int | str | None = None,
    nonce: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Build canonical JSON bytes and Election V2 HMAC headers.

    Payload format: ``{sender_wg}|{timestamp}|{nonce}|{sha256(compact_sorted_json)}``
    with a 32-character lowercase hex nonce by default.
    """
    body_bytes = json.dumps(body_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ts = str(int(time.time()) if timestamp is None else timestamp)
    nonce_val = uuid.uuid4().hex if nonce is None else str(nonce)
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    payload = f"{sender_wg}|{ts}|{nonce_val}|{body_hash}"
    sig = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return body_bytes, {
        "X-Election-Signature": sig,
        "X-Request-Timestamp": ts,
        "X-Election-Nonce": nonce_val,
    }


def load_compose_yaml(rel_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and parse a Docker Compose YAML file as a dict."""
    target = Path(rel_path) if Path(rel_path).is_absolute() else repo_path(rel_path)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"Expected YAML dict in {target}, got {type(data).__name__}"
    return data


def _brace_delta(line: str, in_backtick: bool = False) -> tuple[int, bool]:
    """Count net curly-brace depth change outside quoted strings and # comments."""
    in_double = False
    escaped = False
    delta = 0
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_backtick:
            escaped = True
            continue
        if ch == "`" and not in_double:
            in_backtick = not in_backtick
            continue
        if ch == '"' and not in_backtick:
            in_double = not in_double
            continue
        if not in_backtick and not in_double:
            if ch == "#" and (idx == 0 or line[idx - 1].isspace()):
                break
            if ch == "{":
                delta += 1
            elif ch == "}":
                delta -= 1
    return delta, in_backtick


def extract_caddy_snippet(caddyfile: str, name: str) -> str:
    """Extract a named Caddy snippet ``(name) { ... }`` using brace depth tracking."""
    pattern = re.compile(rf"^\({re.escape(name)}\)\s*\{{\s*$")
    lines = caddyfile.splitlines()
    collecting = False
    depth = 0
    in_backtick = False
    collected: list[str] = []

    for line in lines:
        if not collecting:
            if pattern.match(line):
                collecting = True
                collected.append(line)
                delta, in_backtick = _brace_delta(line, in_backtick)
                depth = delta
                if depth <= 0:
                    break
        else:
            collected.append(line)
            delta, in_backtick = _brace_delta(line, in_backtick)
            depth += delta
            if depth <= 0:
                break

    return "\n".join(collected) if collecting else ""


def extract_caddy_site_blocks(caddyfile: str) -> dict[str, str]:
    """Extract top-level Caddy site blocks keyed by header label (excluding global {} and snippets)."""
    lines = caddyfile.splitlines()
    blocks: dict[str, str] = {}
    current_label: str | None = None
    collected: list[str] = []
    depth = 0
    in_backtick = False

    for line in lines:
        if current_label is None:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Column-0 block opener
            if not line[0].isspace() and stripped.endswith("{"):
                # Exclude global options block `{` and snippet `(name) {`
                if stripped == "{" or stripped.startswith("("):
                    # Still skip over their body so inner lines aren't parsed as top-level
                    current_label = "__SKIP__"
                    collected = [line]
                    delta, in_backtick = _brace_delta(line, in_backtick)
                    depth = delta
                    if depth <= 0:
                        current_label = None
                        collected = []
                        depth = 0
                    continue
                label = stripped[:-1].strip()
                current_label = label
                collected = [line]
                delta, in_backtick = _brace_delta(line, in_backtick)
                depth = delta
                if depth <= 0:
                    blocks[current_label] = "\n".join(collected)
                    current_label = None
                    collected = []
                    depth = 0
        else:
            collected.append(line)
            delta, in_backtick = _brace_delta(line, in_backtick)
            depth += delta
            if depth <= 0:
                if current_label != "__SKIP__":
                    blocks[current_label] = "\n".join(collected)
                current_label = None
                collected = []
                depth = 0

    return blocks


def run_wsl_bash_check(
    script_snippet: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a bash script snippet directly inside WSL/Linux or via wsl.exe from Windows."""
    run_cwd = cwd or REPO_ROOT
    run_env = {**os.environ, **(env or {})}
    if sys.platform != "win32":
        bash_bin = shutil.which("bash") or "/bin/bash"
        return subprocess.run(
            [bash_bin, "-c", script_snippet],
            cwd=run_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return subprocess.run(
        ["wsl", "bash", "-c", script_snippet],
        cwd=run_cwd,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
