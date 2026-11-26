#!/usr/bin/env python3
"""
SMSLY Hosting CLI — The beat-Railway tool.

Usage:
    smsly login <url> [<token>]
    smsly init                      # Interactively create/link service
    smsly link <service_id>         # Link current dir to service
    smsly up                        # Deploy current directory
    smsly env list                  # List environment variables
    smsly env set KEY=VAL ...       # Set environment variables
    smsly logs <id> [--follow]      # View/stream logs
    smsly services list             # List all services
    smsly deploy <id> [--ref REF]   # Trigger git deployment
"""
import argparse
import fnmatch
import getpass
import json
import os
import re
import sys
import tempfile
import urllib.parse
import zipfile

# Try to import requests
try:
    import requests
except ImportError:
    print("Error: 'requests' package is required.")
    print("Install it: pip install requests")
    sys.exit(1)


# ─── Config ─────────────────────────────────────────────────────────────────
CONFIG_DIR = os.path.expanduser("~/.smsly")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PROJECT_FILE = "smsly.json"

IGNORE_DIRS = {
    ".git", ".next", ".turbo", ".terraform",
    "node_modules", "__pycache__", "venv", ".venv",
    "dist", "build", "coverage", "target",
}

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

IGNORE_PATTERNS = [
    ".DS_Store", "*.pyc", "*.log", "*.pem", "*.key", PROJECT_FILE,
]


def _should_ignore_file(filename):
    """Return True if a file should be excluded from source archives."""
    if filename == ".env.example":
        return False
    if filename == ".env" or filename.startswith(".env."):
        return True
    return any(fnmatch.fnmatch(filename, p) for p in IGNORE_PATTERNS)


def _validate_resource_id(value, label="id"):
    """Validate and URL-encode a resource identifier to prevent path traversal."""
    val = str(value or "").strip()
    if not _RESOURCE_ID_RE.match(val):
        print(f"✗ Invalid {label}: must match ^[A-Za-z0-9_-]{{1,128}}$")
        sys.exit(1)
    return urllib.parse.quote(val, safe="")


def _normalize_server_url(raw_url, allow_insecure_http=False):
    """Normalize server URL; default to https:// and block non-loopback http:// unless allowed."""
    candidate = str(raw_url or "").strip().rstrip("/")
    if not candidate:
        print("✗ Server URL is required.")
        sys.exit(1)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:/|$))", candidate) and not re.match(r"^https?://", candidate, re.IGNORECASE):
        print(f"✗ Unsupported URL scheme: {candidate.split(':', 1)[0]}")
        sys.exit(1)
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
        candidate = f"https://{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        print(f"✗ Invalid server URL: {raw_url}")
        sys.exit(1)
    host = parsed.hostname.lower()
    insecure_allowed = bool(allow_insecure_http or os.environ.get("SMSLY_ALLOW_INSECURE_HTTP") == "1")
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS and not insecure_allowed:
        print(
            f"✗ Refusing plaintext http:// for non-loopback host '{host}'. "
            "Use https:// or pass --allow-insecure-http (or set SMSLY_ALLOW_INSECURE_HTTP=1)."
        )
        sys.exit(1)
    return candidate


def _validate_api_path(path):
    """Ensure API paths stay under /api/ without authority/protocol confusion."""
    path_str = str(path or "")
    if not path_str.startswith("/api/") or path_str.startswith("//"):
        print(f"✗ Refusing unsafe API path: {path_str}")
        sys.exit(1)
    return path_str


def _validate_next_path(next_url, base_url=None):
    """Validate pagination next URLs against base host and /api/ prefix."""
    parsed = urllib.parse.urlparse(str(next_url or ""))
    if parsed.netloc and base_url:
        base_netloc = urllib.parse.urlparse(str(base_url)).netloc.lower()
        if parsed.netloc.lower() != base_netloc:
            print(f"✗ Refusing cross-origin pagination URL: {next_url}")
            sys.exit(1)
    next_path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    return _validate_api_path(next_path)


def _is_symlink_or_junction(path):
    """Return True if path is a symlink or a Windows NTFS junction."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if callable(isjunction) and isjunction(path):
        return True
    return False


def save_config(url, token, allow_insecure_http=False):
    """Save API URL and auth token to config file."""
    clean_url = _normalize_server_url(url, allow_insecure_http=allow_insecure_http)
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(
            {
                "url": clean_url,
                "serverUrl": clean_url,
                "token": token,
                "allow_insecure_http": bool(allow_insecure_http),
                "allowInsecureHttp": bool(allow_insecure_http),
            },
            f,
            indent=2,
        )
    # Secure permissions (read/write for owner only)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass  # Windows might fail here
    print(f"✓ Logged in to {clean_url}")


def load_config():
    """Load saved config, or auto-discover if running on host."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                raw_url = (cfg.get("url") or cfg.get("serverUrl") or "").rstrip("/")
                allow_insecure = bool(
                    cfg.get("allow_insecure_http") or cfg.get("allowInsecureHttp")
                )
                if raw_url:
                    resolved_url = _normalize_server_url(
                        raw_url, allow_insecure_http=allow_insecure
                    )
                    cfg["url"] = resolved_url
                    cfg["serverUrl"] = resolved_url
            return cfg
        except (OSError, ValueError) as exc:
            print(f"✗ Failed to read {CONFIG_FILE}: {exc}")
            sys.exit(1)

    # Auto-discovery for local installation
    LOCAL_INSTALL_DIR = "/opt/smsly-hosting"
    ENV_FILE = os.path.join(LOCAL_INSTALL_DIR, ".env")
    TOKEN_FILE = os.path.join(LOCAL_INSTALL_DIR, ".token")

    if os.path.exists(ENV_FILE) and os.path.exists(TOKEN_FILE):
        env_vars = {}
        with open(ENV_FILE, "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()

        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()

        domain = env_vars.get("DOMAIN", "localhost")
        is_localhost = domain in ("localhost", "127.0.0.1", "::1")
        default_ssl = "false" if is_localhost else "true"
        use_ssl = env_vars.get("USE_SSL", default_ssl).lower() == "true"
        scheme = "https" if use_ssl and not is_localhost else "http"

        # If it's a raw IP, use it. If it's a domain, use it.
        # Default to 8090 for local IP mode.
        port = ""
        if domain == "localhost" or domain.replace(".", "").isdigit():
            port = ":8090"

        url = f"{scheme}://{domain}{port}"

        # Best effort: auto-save to user config for future runs
        try:
            save_config(url, token)
        except Exception:
            pass

        return {"url": url, "token": token}

    print("✗ Not logged in. Run: smsly login <url> [<token>]")
    sys.exit(1)


def load_project_config():
    """Load project config (smsly.json) to get service_id."""
    if not os.path.exists(PROJECT_FILE):
        return None
    try:
        with open(PROJECT_FILE, "r") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        print(f"✗ Failed to read {PROJECT_FILE}: {exc}")
        return None


def save_project_config(service_id, name):
    """Save service linkage to smsly.json."""
    with open(PROJECT_FILE, "w") as f:
        json.dump({"service_id": service_id, "name": name}, f, indent=2)
    print(f"✓ Linked to service {name} ({service_id})")


def safe_json(resp, default=None):
    """Parse JSON from an HTTP response without raising on invalid payloads."""
    try:
        return resp.json()
    except ValueError:
        return default


def api_request(method, path, data=None, files=None, stream=False):
    """Make an authenticated API request."""
    config = load_config()
    raw_base_url = str(config.get("url") or config.get("serverUrl") or "").rstrip("/")
    if not raw_base_url:
        print("✗ Missing server URL in config. Run: smsly login <url>")
        sys.exit(1)
    allow_insecure = bool(
        config.get("allow_insecure_http") or config.get("allowInsecureHttp")
    )
    base_url = _normalize_server_url(raw_base_url, allow_insecure_http=allow_insecure)
    safe_path = _validate_api_path(path)
    url = f"{base_url}{safe_path}"
    raw_token = str(config.get("token", "") or "").strip()
    if not raw_token:
        print("✗ API token is empty or missing. Run: smsly login <url>")
        sys.exit(1)
    if raw_token.lower().startswith(("bearer ", "token ")):
        auth_header = raw_token
    elif raw_token.startswith(("smsly_", "sk_")) or (raw_token.startswith("eyJ") and raw_token.count(".") == 2):
        auth_header = f"Bearer {raw_token}"
    else:
        auth_header = f"Token {raw_token}"
    headers = {
        "Authorization": auth_header,
        "X-Requested-With": "XMLHttpRequest",
    }
    # Don't set Content-Type if uploading files (requests handles boundary)
    if not files:
        headers["Content-Type"] = "application/json"

    try:
        resp = requests.request(
            method, url, headers=headers, files=files, stream=stream, timeout=60,
            **({"data": data} if files else {"json": data})
        )
        return resp
    except requests.RequestException as exc:
        print(f"✗ Request to {base_url} failed: {exc}")
        sys.exit(1)


def _fetch_all_services():
    """Fetch all services across paginated /api/v1/services/?page_size=100 responses."""
    config = load_config()
    base_url = str(config.get("url") or config.get("serverUrl") or "").rstrip("/")
    services = []
    next_path = "/api/v1/services/?page_size=100"
    for _ in range(50):
        if not next_path:
            break
        resp = api_request("GET", next_path)
        if resp.status_code != 200:
            print(f"✗ Error: {resp.status_code} — {resp.text}")
            sys.exit(1)
        payload = safe_json(resp)
        if payload is None:
            print("✗ Invalid JSON response from server.")
            sys.exit(1)
        if isinstance(payload, list):
            services.extend(payload)
            break
        if isinstance(payload, dict):
            services.extend(payload.get("results", []))
            next_url = payload.get("next")
            if next_url:
                next_path = _validate_next_path(next_url, base_url=base_url)
            else:
                next_path = None
        else:
            break
    return services


# ─── Commands ───────────────────────────────────────────────────────────────

def cmd_login(args):
    """Login to SMSLY Hosting."""
    allow_insecure = bool(getattr(args, "allow_insecure_http", False))
    normalized_url = _normalize_server_url(
        args.url,
        allow_insecure_http=allow_insecure,
    )
    token = (args.token or os.environ.get("SMSLY_TOKEN") or "").strip()
    if not token:
        try:
            token = getpass.getpass("API Token: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n✗ Cancelled.")
            sys.exit(1)
    if not token:
        print("✗ API token is required.")
        sys.exit(1)
    save_config(normalized_url, token, allow_insecure_http=allow_insecure)


def cmd_services_list(args):
    """List all services."""
    services = _fetch_all_services()

    if not services:
        print("  No services found.")
        return

    print(f"{'NAME':<25} {'STATUS':<14} {'TYPE':<8} {'ID'}")
    print("─" * 82)
    for svc in services:
        name = svc.get("name", "?")
        latest = svc.get("latest_deployment") or {}
        svc_status = (latest.get("status") if isinstance(latest, dict) else None) or svc.get("status") or "UNKNOWN"
        dtype = svc.get("deploy_type", "GIT")
        svc_id = svc.get("id", "?")
        print(f"{name:<25} {svc_status:<14} {dtype:<8} {svc_id}")


def cmd_init(args):
    """Initialize a project (link or create)."""
    if os.path.exists(PROJECT_FILE):
        print(f"✗ {PROJECT_FILE} already exists.")
        sys.exit(1)

    print("Initialize SMSLY Project")
    print("1. Link to existing service")
    print("2. Create new service")
    choice = input("Select [1/2]: ").strip()

    if choice == "1":
        # List services to pick
        services = _fetch_all_services()

        if not services:
            print("No services found.")
            sys.exit(1)

        print("\nAvailable Services:")
        for idx, s in enumerate(services):
            print(f"{idx+1}. {s['name']} ({s['id']})")

        try:
            sel = int(input("\nSelect service number: ")) - 1
            if 0 <= sel < len(services):
                save_project_config(services[sel]['id'], services[sel]['name'])
            else:
                print("Invalid selection.")
                sys.exit(1)
        except ValueError:
            print("Invalid input.")
            sys.exit(1)

    elif choice == "2":
        name = input("Service Name: ").strip()
        if not name:
            print("Name required.")
            sys.exit(1)

        print("Creating service...")
        data = {
            "name": name,
            "deploy_type": "DOCKER",
            "cpu_cores": 0.5,
            "memory_mb": 512
        }
        resp = api_request("POST", "/api/v1/services/", data=data)
        if resp.status_code == 201:
            svc = safe_json(resp)
            if not isinstance(svc, dict) or not svc.get("id"):
                print("✗ Unexpected server response when creating service.")
                sys.exit(1)
            save_project_config(svc['id'], svc['name'])
        else:
            print(f"✗ Failed to create service: {resp.text}")
            sys.exit(1)
    else:
        print("Cancelled.")
        sys.exit(1)


def cmd_link(args):
    """Link directory to a service ID."""
    safe_service_id = _validate_resource_id(args.service_id, "service_id")
    save_project_config(safe_service_id, "Linked Service")


def cmd_up(args):
    """Zip and upload current directory."""
    project = load_project_config()
    if not project:
        print(f"✗ No {PROJECT_FILE} found. Run 'smsly init' or 'smsly link'.")
        sys.exit(1)

    service_id = _validate_resource_id(project.get('service_id'), "service_id")

    print(f"Packaging current directory for service {service_id}...")

    # Create temp zip
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_name = tmp.name

    tmp_basename = os.path.basename(tmp_name)

    try:
        with zipfile.ZipFile(tmp_name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk('.', followlinks=False):
                # Filter directories and skip symlinked/junction directories
                dirs[:] = [
                    d for d in dirs
                    if d not in IGNORE_DIRS and not _is_symlink_or_junction(os.path.join(root, d))
                ]

                for file in files:
                    if file == tmp_basename:
                        continue
                    file_path = os.path.join(root, file)
                    if _is_symlink_or_junction(file_path):
                        continue
                    if _should_ignore_file(file):
                        continue

                    arcname = os.path.relpath(file_path, '.').replace(os.sep, '/').replace('\\', '/')
                    zf.write(file_path, arcname)

        file_size = os.path.getsize(tmp_name)
        if file_size > MAX_UPLOAD_BYTES:
            print(
                f"✗ Archive too large ({file_size / 1024 / 1024:.2f} MB). "
                f"Maximum upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            )
            sys.exit(1)
        print(f"Uploading {file_size / 1024 / 1024:.2f} MB package...")

        with open(tmp_name, 'rb') as f:
            resp = api_request(
                "POST",
                "/api/v1/deployments/upload/",
                data={"service_id": service_id},
                files={"file": ("source.zip", f)}
            )

        if resp.status_code == 201:
            data = safe_json(resp) or {}
            deploy_id = data.get('deployment_id')
            print("✓ Upload successful!")
            print(f"✓ Deployment triggered: {deploy_id}")
            print(f"  Run 'smsly logs {deploy_id} --follow' to watch.")
        else:
            print(f"✗ Upload failed: {resp.status_code} - {resp.text}")
            sys.exit(1)

    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def cmd_deploy(args):
    """Trigger deployment for a service."""
    raw_service_id = args.service_id
    if not raw_service_id:
        project = load_project_config()
        if not project:
            print(f"✗ Provide <service_id> or run inside a directory with {PROJECT_FILE}.")
            sys.exit(1)
        raw_service_id = project.get("service_id")

    service_id = _validate_resource_id(raw_service_id, "service_id")
    payload = {"ref": args.ref or "HEAD"}
    resp = api_request("POST", f"/api/v1/services/{service_id}/deploy/", data=payload)
    data = safe_json(resp)

    if resp.status_code in (200, 201, 202):
        deploy_id = (data or {}).get("deployment_id") or (data or {}).get("id")
        print(f"✓ Deployment triggered for service {service_id}")
        if deploy_id:
            print(f"  Deployment ID: {deploy_id}")
            print(f"  Run 'smsly logs {deploy_id} --follow' to watch.")
    elif resp.status_code == 409 and isinstance(data, dict) and data.get("existing_deployment"):
        existing = data["existing_deployment"]
        print(f"! Deployment already in progress: {existing.get('id')} ({existing.get('status')})")
    else:
        print(f"✗ Deploy failed: {resp.status_code} — {resp.text}")
        sys.exit(1)


def cmd_env(args):
    """Manage environment variables."""
    project = load_project_config()
    if not project:
        print("✗ Not inside a linked project. Run 'smsly link <id>'.")
        sys.exit(1)
    service_id = _validate_resource_id(project.get('service_id'), "service_id")

    if args.env_cmd == "list":
        resp = api_request("GET", f"/api/v1/services/{service_id}/env_vars/")
        if resp.status_code == 200:
            payload = safe_json(resp)
            if payload is None:
                print("✗ Invalid JSON response from server.")
                sys.exit(1)
            vars_list = payload if isinstance(payload, list) else (payload.get("results", []) if isinstance(payload, dict) else [])
            for v in vars_list:
                val = v.get('value', '')
                if v.get('is_secret'):
                    val = "********"
                print(f"{v.get('key')}={val}")
        else:
            print(f"✗ Error: {resp.text}")
            sys.exit(1)

    elif args.env_cmd == "set":
        if not args.vars:
            print("Usage: smsly env set KEY=VAL KEY2=VAL2")
            sys.exit(1)

        had_error = False
        for item in args.vars:
            if "=" not in item:
                print(f"Skipping invalid format '{item}'")
                had_error = True
                continue
            k, v = item.split("=", 1)
            # Create/Update each var
            # Ideal API would accept batch, but we'll loop for now
            payload = {"key": k, "value": v}
            resp = api_request("POST", f"/api/v1/services/{service_id}/env_vars/", data=payload)
            if resp.status_code in (200, 201):
                print(f"✓ Set {k}")
            else:
                print(f"✗ Failed to set {k}: {resp.text}")
                had_error = True
        if had_error:
            sys.exit(1)
    else:
        print("Usage: smsly env [list|set]")
        sys.exit(1)


def cmd_logs(args):
    """View or follow logs."""
    deploy_id = _validate_resource_id(args.deployment_id, "deployment_id")

    # 1. Initial Fetch
    resp = api_request("GET", f"/api/v1/deployments/{deploy_id}/build-logs/")
    if resp.status_code != 200:
        # Fallback to old detailed endpoint if build-logs not found
        resp = api_request("GET", f"/api/v1/deployments/{deploy_id}/")
        if resp.status_code != 200:
            print(f"✗ Error: {resp.status_code} — {resp.text}")
            sys.exit(1)

    data = safe_json(resp)
    if not isinstance(data, dict):
        print("✗ Invalid JSON response from server.")
        sys.exit(1)
    logs = data.get("build_logs", "") or ""
    status = data.get("status", "UNKNOWN")

    # Clear screen if following? No, just print.
    print(logs, end="")

    if args.follow:
        import time
        last_len = len(logs)

        while status in ["QUEUED", "BUILDING", "DEPLOYING"]:
            time.sleep(2)
            resp = api_request("GET", f"/api/v1/deployments/{deploy_id}/build-logs/")
            if resp.status_code == 200:
                data = safe_json(resp)
                if not isinstance(data, dict):
                    break
                status = data.get("status", status)
                current_logs = data.get("build_logs", "") or ""

                if len(current_logs) > last_len:
                    print(current_logs[last_len:], end="", flush=True)
                    last_len = len(current_logs)
            else:
                break

        print(f"\n[Process completed with status: {status}]")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="smsly")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # login
    p_login = subparsers.add_parser("login")
    p_login.add_argument("url")
    p_login.add_argument("token", nargs="?", default=None, help="API token (prompted securely if omitted)")
    p_login.add_argument("--allow-insecure-http", action="store_true", help="Allow plaintext http:// for non-loopback hosts")
    p_login.set_defaults(func=cmd_login)

    # init
    p_init = subparsers.add_parser("init")
    p_init.set_defaults(func=cmd_init)

    # link
    p_link = subparsers.add_parser("link")
    p_link.add_argument("service_id")
    p_link.set_defaults(func=cmd_link)

    # up
    p_up = subparsers.add_parser("up")
    p_up.set_defaults(func=cmd_up)

    # services
    p_svc = subparsers.add_parser("services")
    p_svc.add_argument("list", nargs="?", help="List services") # Hacky subcmd
    p_svc.set_defaults(func=cmd_services_list)

    # env
    p_env = subparsers.add_parser("env")
    env_subs = p_env.add_subparsers(dest="env_cmd")
    env_subs.add_parser("list")
    p_env_set = env_subs.add_parser("set")
    p_env_set.add_argument("vars", nargs="+", help="KEY=VAL pairs")
    p_env.set_defaults(func=cmd_env)

    # deploy
    p_deploy = subparsers.add_parser("deploy")
    p_deploy.add_argument("service_id", nargs="?", default=None)
    p_deploy.add_argument("--ref", default="HEAD")
    p_deploy.set_defaults(func=cmd_deploy)

    # logs
    p_logs = subparsers.add_parser("logs")
    p_logs.add_argument("deployment_id")
    p_logs.add_argument("--follow", "-f", action="store_true")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
