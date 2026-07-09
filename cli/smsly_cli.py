#!/usr/bin/env python3
"""
SMSLY Hosting CLI — The beat-Railway tool.

Usage:
    smsly login <url> <token>
    smsly init                      # Interactively create/link service
    smsly link <service_id>         # Link current dir to service
    smsly up                        # Deploy current directory
    smsly env list                  # List environment variables
    smsly env set KEY=VAL ...       # Set environment variables
    smsly logs <id> [--follow]      # View/stream logs
    smsly services list             # List all services
    smsly deploy <id>               # Trigger git deployment
"""
import argparse
import glob
import json
import os
import sys
import tempfile
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

IGNORE_PATTERNS = [
    ".git", "node_modules", "__pycache__", "venv", ".env", ".DS_Store",
    "dist", "build", "coverage", "*.pyc", "*.log"
]


def save_config(url, token):
    """Save API URL and auth token to config file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"url": url.rstrip("/"), "token": token}, f)
    # Secure permissions (read/write for owner only)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass  # Windows might fail here
    print(f"✓ Logged in to {url}")


def load_config():
    """Load saved config, or auto-discover if running on host."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

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
        use_ssl = env_vars.get("USE_SSL", "false").lower() == "true"
        scheme = "https" if use_ssl and domain != "localhost" else "http"

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

    print("✗ Not logged in. Run: smsly login <url> <token>")
    sys.exit(1)


def load_project_config():
    """Load project config (smsly.json) to get service_id."""
    if not os.path.exists(PROJECT_FILE):
        return None
    with open(PROJECT_FILE, "r") as f:
        return json.load(f)


def save_project_config(service_id, name):
    """Save service linkage to smsly.json."""
    with open(PROJECT_FILE, "w") as f:
        json.dump({"service_id": service_id, "name": name}, f, indent=2)
    print(f"✓ Linked to service {name} ({service_id})")


def api_request(method, path, data=None, files=None, stream=False):
    """Make an authenticated API request."""
    config = load_config()
    url = f"{config['url']}{path}"
    headers = {
        "Authorization": f"Token {config['token']}",
    }
    # Don't set Content-Type if uploading files (requests handles boundary)
    if not files:
        headers["Content-Type"] = "application/json"

    try:
        resp = requests.request(
            method, url, headers=headers, json=data, files=files, stream=stream, timeout=60
        )
        return resp
    except requests.ConnectionError:
        print(f"✗ Cannot connect to {config['url']}")
        sys.exit(1)


# ─── Commands ───────────────────────────────────────────────────────────────

def cmd_login(args):
    """Login to SMSLY Hosting."""
    save_config(args.url, args.token)


def cmd_services_list(args):
    """List all services."""
    resp = api_request("GET", "/api/v1/services/")
    if resp.status_code != 200:
        print(f"✗ Error: {resp.status_code} — {resp.text}")
        return

    services = resp.json()
    if isinstance(services, dict):
        services = services.get("results", [])

    if not services:
        print("  No services found.")
        return

    print(f"{'NAME':<25} {'STATUS':<12} {'TYPE':<8} {'ID'}")
    print("─" * 80)
    for svc in services:
        name = svc.get("name", "?")
        # Status isn't direct on Service, usually on latest deploy
        # We'll just show name and type for now
        dtype = svc.get("deploy_type", "GIT")
        svc_id = svc.get("id", "?")
        print(f"{name:<25} {'---':<12} {dtype:<8} {svc_id}")


def cmd_init(args):
    """Initialize a project (link or create)."""
    if os.path.exists(PROJECT_FILE):
        print(f"✗ {PROJECT_FILE} already exists.")
        return

    print("Initialize SMSLY Project")
    print("1. Link to existing service")
    print("2. Create new service")
    choice = input("Select [1/2]: ").strip()

    if choice == "1":
        # List services to pick
        resp = api_request("GET", "/api/v1/services/")
        services = resp.json().get("results", []) if resp.status_code == 200 else []

        if not services:
            print("No services found.")
            return

        print("\nAvailable Services:")
        for idx, s in enumerate(services):
            print(f"{idx+1}. {s['name']} ({s['id']})")

        try:
            sel = int(input("\nSelect service number: ")) - 1
            if 0 <= sel < len(services):
                save_project_config(services[sel]['id'], services[sel]['name'])
            else:
                print("Invalid selection.")
        except ValueError:
            print("Invalid input.")

    elif choice == "2":
        name = input("Service Name: ").strip()
        if not name:
            print("Name required.")
            return

        print("Creating service...")
        data = {
            "name": name,
            "deploy_type": "UPLOAD",
            "cpu_cores": 0.5,
            "memory_mb": 512
        }
        resp = api_request("POST", "/api/v1/services/", data=data)
        if resp.status_code == 201:
            svc = resp.json()
            save_project_config(svc['id'], svc['name'])
        else:
            print(f"✗ Failed to create service: {resp.text}")
    else:
        print("Cancelled.")


def cmd_link(args):
    """Link directory to a service ID."""
    save_project_config(args.service_id, "Linked Service")


def cmd_up(args):
    """Zip and upload current directory."""
    project = load_project_config()
    if not project:
        print(f"✗ No {PROJECT_FILE} found. Run 'smsly init' or 'smsly link'.")
        return

    service_id = project['service_id']

    print(f"Packaging current directory for service {service_id}...")

    # Create temp zip
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_name = tmp.name

    try:
        with zipfile.ZipFile(tmp_name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk('.'):
                # Filter directories
                dirs[:] = [d for d in dirs if d not in IGNORE_PATTERNS]

                for file in files:
                    # Filter files
                    if any(glob.fnmatch.fnmatch(file, p) for p in IGNORE_PATTERNS):
                        continue

                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, '.')
                    zf.write(file_path, arcname)

        file_size = os.path.getsize(tmp_name)
        print(f"Uploading {file_size / 1024 / 1024:.2f} MB package...")

        with open(tmp_name, 'rb') as f:
            resp = api_request(
                "POST",
                "/api/v1/deployments/upload/",
                data={"service_id": service_id},
                files={"file": ("source.zip", f)}
            )

        if resp.status_code == 201:
            data = resp.json()
            deploy_id = data.get('deployment_id')
            print("✓ Upload successful!")
            print(f"✓ Deployment triggered: {deploy_id}")
            print(f"  Run 'smsly logs {deploy_id} --follow' to watch.")
        else:
            print(f"✗ Upload failed: {resp.status_code} - {resp.text}")

    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def cmd_env(args):
    """Manage environment variables."""
    project = load_project_config()
    if not project:
        print("✗ Not inside a linked project. Run 'smsly link <id>'.")
        return
    service_id = project['service_id']

    if args.env_cmd == "list":
        resp = api_request("GET", f"/api/v1/services/{service_id}/env_vars/")
        if resp.status_code == 200:
            vars = resp.json()
            for v in vars:
                val = v['value']
                if v.get('is_secret'):
                    val = "********"
                print(f"{v['key']}={val}")
        else:
            print(f"✗ Error: {resp.text}")

    elif args.env_cmd == "set":
        if not args.vars:
            print("Usage: smsly env set KEY=VAL KEY2=VAL2")
            return

        for item in args.vars:
            if "=" not in item:
                print(f"Skipping invalid format '{item}'")
                continue
            k, v = item.split("=", 1)
            # Create/Update each var
            # Ideal API would accept batch, but we'll loop for now
            payload = {"key": k, "value": v, "is_secret": False}
            resp = api_request("POST", f"/api/v1/services/{service_id}/env_vars/", data=payload)
            if resp.status_code == 201:
                print(f"✓ Set {k}")
            else:
                print(f"✗ Failed to set {k}: {resp.text}")


def cmd_logs(args):
    """View or follow logs."""
    deploy_id = args.deployment_id

    # 1. Initial Fetch
    resp = api_request("GET", f"/api/v1/deployments/{deploy_id}/build-logs/")
    if resp.status_code != 200:
        # Fallback to old detailed endpoint if build-logs not found
        resp = api_request("GET", f"/api/v1/deployments/{deploy_id}/")
        if resp.status_code != 200:
            print(f"✗ Error: {resp.status_code} — {resp.text}")
            return

    data = resp.json()
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
                data = resp.json()
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
    p_login.add_argument("token")
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
    p_env_list = env_subs.add_parser("list")
    p_env_set = env_subs.add_parser("set")
    p_env_set.add_argument("vars", nargs="+", help="KEY=VAL pairs")
    p_env.set_defaults(func=cmd_env)

    # deploy
    p_deploy = subparsers.add_parser("deploy")
    p_deploy.add_argument("service_id")
    p_deploy.add_argument("--ref")
    p_deploy.set_defaults(func=lambda a: print("Use 'smsly up' for local deploy or implement API trigger"))

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
