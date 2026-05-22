#!/usr/bin/env python3
"""
SMSLY Deploy Agent — K3s / Helm Deployment Agent

Manages the lifecycle of the SMSLY Hosting control plane on a K3s cluster
via Helm charts. Supports rolling migration from legacy Docker Compose.

Usage:
  python deploy-agent.py install          Install/upgrade Helm chart
  python deploy-agent.py status           Show deployment status
  python deploy-agent.py migrate          Migrate from Docker Compose to K3s
  python deploy-agent.py rollback         Rollback to previous Helm revision
  python deploy-agent.py diff             Show diff between Compose and Helm
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
CHART_PATH = REPO_ROOT / "charts" / "smsly-hosting"
NAMESPACE = "smsly-system"
RELEASE_NAME = "smsly-hosting"
HELM_LOCK = "/tmp/smsly-helm.lock"


class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def ok(msg):
    print(f"{Colors.GREEN}✓{Colors.RESET} {msg}")


def info(msg):
    print(f"{Colors.CYAN}ℹ{Colors.RESET} {msg}")


def warn(msg):
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {msg}")


def fail(msg):
    print(f"{Colors.RED}✗{Colors.RESET} {msg}", file=sys.stderr)


def _run(cmd: list[str], check=True, capture=False) -> subprocess.CompletedProcess:
    """Run a command, print it, and return the result."""
    print(f"{Colors.BOLD}$ {' '.join(cmd)}{Colors.RESET}")
    try:
        if capture:
            return subprocess.run(cmd, capture_output=True, text=True, check=check)
        return subprocess.run(cmd, check=check)
    except subprocess.CalledProcessError as e:
        fail(f"Command failed with exit code {e.returncode}")
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        if check:
            sys.exit(1)
        return e


def _check_prerequisites():
    """Verify all required tools are installed."""
    missing = []
    for tool in ["helm", "kubectl", "docker"]:
        if not _run(["which", tool], check=False, capture=True).stdout.strip():
            missing.append(tool)
    if missing:
        fail(f"Missing required tools: {', '.join(missing)}")
        fail("Install them first: https://k3s.io / https://helm.sh")
        sys.exit(1)
    ok("All prerequisites (helm, kubectl, docker) found")


def _detect_k3s() -> bool:
    """Detect if we're running on a K3s cluster."""
    result = _run(["kubectl", "get", "nodes", "--no-headers"], check=False, capture=True)
    if result.returncode != 0:
        return False
    nodes = result.stdout.strip().split("\n")
    if not nodes or not nodes[0].strip():
        return False
    ok(f"K3s cluster detected ({len(nodes)} node(s))")
    return True


def _detect_compose() -> bool:
    """Detect if Docker Compose services are running."""
    result = _run(
        ["docker", "compose", "ps", "--format", "json", "--status", "running"],
        check=False, capture=True
    )
    return bool(result.stdout.strip())


def _get_migration_state() -> dict:
    """Read migration state from a local file."""
    state_file = REPO_ROOT / ".migration-state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {"phase": "compose", "helm_revision": 0, "last_migration_step": None}


def _save_migration_state(state: dict):
    """Write migration state to a local file."""
    state_file = REPO_ROOT / ".migration-state.json"
    state_file.write_text(json.dumps(state, indent=2))
    ok(f"Migration state saved (phase: {state['phase']})")


# ── CLI Commands ──────────────────────────────────────────────────────────────


def cmd_install(args):
    """Install or upgrade the Helm chart."""
    _check_prerequisites()

    if not _detect_k3s():
        fail("No K3s cluster detected. Run `k3s` or set KUBECONFIG first.")
        sys.exit(1)

    info(f"Installing/upgrading Helm release '{RELEASE_NAME}' in namespace '{NAMESPACE}'")

    _run([
        "kubectl", "create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml"
    ], capture=True)
    _run(["kubectl", "apply", "-f", "-"], input="", capture=True)

    helm_args = [
        "helm", "upgrade", "--install", RELEASE_NAME, str(CHART_PATH),
        "--namespace", NAMESPACE,
        "--create-namespace",
        "--timeout", "15m",
        "--wait",
    ]

    if args.values:
        for v in args.values:
            helm_args.extend(["-f", v])

    if args.set:
        for s in args.set:
            helm_args.extend(["--set", s])

    result = _run(helm_args)

    state = _get_migration_state()
    rev_result = _run([
        "helm", "list", "--namespace", NAMESPACE,
        "--filter", RELEASE_NAME, "-o", "json"
    ], capture=True)
    try:
        releases = json.loads(rev_result.stdout)
        if releases:
            state["helm_revision"] = int(releases[0].get("revision", 0))
    except (json.JSONDecodeError, KeyError, IndexError):
        pass
    state["phase"] = "helm"
    _save_migration_state(state)

    ok(f"Helm release '{RELEASE_NAME}' deployed (revision {state['helm_revision']})")


def cmd_status(args):
    """Show current deployment status across both modes."""
    _check_prerequisites()

    state = _get_migration_state()
    print(f"\n{Colors.BOLD}Migration Phase:{Colors.RESET} {state.get('phase', 'unknown')}")
    print(f"{Colors.BOLD}Helm Revision:{Colors.RESET} {state.get('helm_revision', 0)}")
    print(f"{Colors.BOLD}Last Migration Step:{Colors.RESET} {state.get('last_migration_step', 'N/A')}")
    print()

    if _detect_compose():
        info("Docker Compose services are RUNNING")
        _run(["docker", "compose", "ps", "--status", "running"], check=False)
    else:
        info("No Docker Compose services detected")

    print()

    if _detect_k3s():
        info("K3s cluster is AVAILABLE")
        _run(["kubectl", "get", "nodes", "-o", "wide"], check=False)

        result = _run([
            "helm", "list", "--namespace", NAMESPACE,
            "--filter", RELEASE_NAME, "-o", "json"
        ], check=False, capture=True)
        if result.stdout.strip():
            releases = json.loads(result.stdout)
            if releases:
                ok(f"Helm release '{RELEASE_NAME}' found (rev {releases[0].get('revision')})")
                _run([
                    "kubectl", "get", "all",
                    "-n", NAMESPACE,
                    "-l", f"app.kubernetes.io/instance={RELEASE_NAME}",
                ], check=False)
            else:
                info("Helm release not installed yet")
        else:
            info("Helm release not installed yet")
    else:
        warn("No K3s cluster detected")
        warn("Provision one first: https://k3s.io")


def cmd_migrate(args):
    """Orchestrate migration from Docker Compose to K3s/Helm."""
    _check_prerequisites()

    if not _detect_k3s():
        fail("No K3s cluster detected. Deploy K3s first.")
        sys.exit(1)

    state = _get_migration_state()

    steps = [
        ("validate", "Validate K3s cluster readiness"),
        ("drain-compose", "Drain active connections from Docker Compose services"),
        ("deploy-helm", "Deploy control plane via Helm (with migration values)"),
        ("verify", "Verify all services are healthy on K3s"),
        ("switch-dns", "Switch DNS / Traefik routing to K3s"),
        ("stop-compose", "Stop legacy Docker Compose services"),
        ("cleanup", "Clean up old Docker Compose volumes (optional)"),
    ]

    start = False
    for name, desc in steps:
        if name == args.step or args.step == "all":
            start = True
        if not start:
            continue

        print(f"\n{Colors.BOLD}[{name}]{Colors.RESET} {desc}")
        state["last_migration_step"] = name

        if name == "validate":
            _run(["kubectl", "cluster-info", "--request-timeout", "10s"], check=False)
            _run(["kubectl", "get", "nodes"], check=False)
            ok("Cluster validation complete")

        elif name == "deploy-helm":
            helm_values = []
            if args.values:
                helm_values = list(args.values)
            compose_env = REPO_ROOT / ".env"
            if compose_env.exists() and not args.values:
                warn("Using .env for migration — consider explicit values.yaml for production")
            _run([
                "helm", "upgrade", "--install", RELEASE_NAME, str(CHART_PATH),
                "--namespace", NAMESPACE,
                "--create-namespace",
                "--timeout", "15m",
                "--wait",
            ] + [f"-f{v}" for v in helm_values])

            rev_result = _run([
                "helm", "list", "--namespace", NAMESPACE,
                "--filter", RELEASE_NAME, "-o", "json"
            ], capture=True)
            try:
                releases = json.loads(rev_result.stdout)
                if releases:
                    state["helm_revision"] = int(releases[0].get("revision", 0))
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        elif name == "verify":
            _run([
                "kubectl", "wait", "--for=condition=available",
                "--timeout=120s", "-n", NAMESPACE,
                "deployment", "-l", f"app.kubernetes.io/instance={RELEASE_NAME}",
            ], check=False)
            _run([
                "kubectl", "get", "pods", "-n", NAMESPACE,
            ], check=False)

        elif name == "switch-dns":
            info("Update DNS records to point to K3s ingress (Traefik) IP")
            info("Get the ingress IP:")
            _run([
                "kubectl", "get", "svc", "-n", "kube-system",
                "traefik", "-o",
                "jsonpath={.status.loadBalancer.ingress[0].ip}",
            ], check=False)

        elif name == "stop-compose":
            _run(["docker", "compose", "down"], check=False)
            ok("Docker Compose services stopped")

        elif name == "cleanup":
            warn("Skipping cleanup — remove old volumes manually when ready")

        elif name == "drain-compose":
            info("Enable maintenance mode on legacy services...")
            warn("Ensure no active deploys are running before proceeding")

        _save_migration_state(state)

    state["phase"] = "helm"
    _save_migration_state(state)
    ok(f"Migration to Helm complete (phase: {state['phase']})")
    info("Run `python deploy-agent.py status` to verify")


def cmd_rollback(args):
    """Rollback Helm release to a previous revision."""
    _check_prerequisites()

    rev = args.revision
    if not rev:
        result = _run([
            "helm", "history", RELEASE_NAME, "--namespace", NAMESPACE,
            "-o", "json"
        ], capture=True)
        revisions = json.loads(result.stdout)
        if len(revisions) < 2:
            fail("No previous revision to rollback to")
            sys.exit(1)
        rev = revisions[-2]["revision"]

    info(f"Rolling back '{RELEASE_NAME}' to revision {rev}")
    _run([
        "helm", "rollback", RELEASE_NAME, str(rev),
        "--namespace", NAMESPACE,
        "--timeout", "10m",
        "--wait",
        "--recreate-pods",
    ])
    ok(f"Rolled back to revision {rev}")


def cmd_diff(args):
    """Show differences between Compose and Helm configuration."""
    _check_prerequisites()

    if not _detect_k3s():
        warn("Not on K3s — can only show Compose state")
    if not _detect_compose():
        warn("No Compose services running — nothing to compare")

    info("Docker Compose services:")
    _run(["docker", "compose", "config", "--services"], check=False)

    print()
    info("Helm chart templates:")
    for t in sorted(CHART_PATH.glob("templates/*.yaml")):
        print(f"  {t.relative_to(CHART_PATH)}")

    print()
    if _detect_k3s():
        info("Helm installed resources:")
        _run([
            "kubectl", "get", "all",
            "-n", NAMESPACE,
            "-l", f"app.kubernetes.io/instance={RELEASE_NAME}",
        ], check=False)

    info("Recommended migration values:")
    print(json.dumps({
        "global": {"domain": os.environ.get("DOMAIN", "hosting.example.com")},
        "backend": {
            "env": {
                "debug": os.environ.get("DEBUG", "False"),
                "secretKey": "*** (set via --set or values file)",
            }
        },
    }, indent=2))


def cmd_build_push(args):
    """Build and push Docker images, then update Helm values."""
    _check_prerequisites()

    registry = args.registry or os.environ.get("REGISTRY", "smsly")
    tag = args.tag or os.environ.get("IMAGE_TAG", "latest")

    components = {
        "backend": REPO_ROOT / "backend",
        "frontend": REPO_ROOT / "frontend",
    }

    for name, path in components.items():
        full_image = f"{registry}/{name}:{tag}"
        info(f"Building {full_image}...")
        _run(["docker", "build", "-t", full_image, str(path), "-f", str(path / "Dockerfile")])

        if args.push:
            info(f"Pushing {full_image}...")
            _run(["docker", "push", full_image])

    if args.update_helm:
        _run([
            "helm", "upgrade", RELEASE_NAME, str(CHART_PATH),
            "--namespace", NAMESPACE,
            "--reuse-values",
            "--set", f"backend.image.tag={tag}",
            "--set", f"frontend.image.tag={tag}",
            "--wait",
        ], check=False)
        ok("Helm release updated with new image tags")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="SMSLY Deploy Agent — K3s/Helm deployment manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    p_install = sub.add_parser("install", help="Install or upgrade the Helm chart")
    p_install.add_argument("-f", "--values", action="append", help="Helm values files")
    p_install.add_argument("--set", action="append", help="Set Helm values on the command line")

    sub.add_parser("status", help="Show deployment status")

    p_migrate = sub.add_parser("migrate", help="Migrate from Docker Compose to K3s/Helm")
    p_migrate.add_argument("step", nargs="?", default="all",
                           help="Migration step or 'all' (default)")
    p_migrate.add_argument("-f", "--values", action="append", help="Helm values files")

    p_rollback = sub.add_parser("rollback", help="Rollback Helm release")
    p_rollback.add_argument("revision", nargs="?", type=int, default=None,
                            help="Revision number (default: previous)")

    sub.add_parser("diff", help="Show Compose vs Helm differences")

    p_build = sub.add_parser("build-push", help="Build and optionally push images")
    p_build.add_argument("--registry", help="Container registry URL")
    p_build.add_argument("--tag", help="Image tag")
    p_build.add_argument("--push", action="store_true", help="Push images after build")
    p_build.add_argument("--update-helm", action="store_true",
                         help="Update Helm release with new tags")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "install": cmd_install,
        "status": cmd_status,
        "migrate": cmd_migrate,
        "rollback": cmd_rollback,
        "diff": cmd_diff,
        "build-push": cmd_build_push,
    }

    lock_fd = None
    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
