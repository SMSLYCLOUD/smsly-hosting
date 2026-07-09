#!/usr/bin/env python
"""
SMSLY Grid - Ecosystem Connectivity Auditor
Checks health, API, and worker heartbeats for all managed nodes.
"""

import os
import sys
import time
from datetime import datetime

import requests

# Setup Django environment
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

try:
    import django
    django.setup()
except Exception as e:
    print(f"Error setting up Django: {e}")
    sys.exit(1)

from apps.deployments.models import Deployment, ManagedServer
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


def color(text, code):
    return f"\033[{code}m{text}\033[0m"

def success(msg): print(f"  {color('✅', '32')} {msg}")
def failure(msg): print(f"  {color('❌', '31')} {msg}")
def warning(msg): print(f"  {color('⚠️', '33')} {msg}")
def info(msg): print(f"  {color('ℹ️', '34')} {msg}")

def audit_node(server):
    print(f"\n{color('='*60, '36')}")
    print(f"AUDITING NODE: {color(server.name, '1')} ({server.host})")
    print(f"{color('='*60, '36')}")

    orch = RemoteOrchestrator(server)
    
    # 1. Basic HTTP Health
    try:
        base_url = server.api_url or f"http://{server.host}"
        health_url = f"{base_url.rstrip('/')}/health"
        start = time.monotonic()
        resp = requests.get(health_url, timeout=10)
        elapsed = (time.monotonic() - start) * 1000
        
        if resp.status_code == 200:
            success(f"HTTP Health OK ({elapsed:.0f}ms)")
        else:
            failure(f"HTTP Health returned {resp.status_code}")
    except Exception as e:
        failure(f"HTTP Health Unreachable: {e}")

    # 2. API Connectivity (Auth Check)
    try:
        api_path = "/api/v1/services/"
        resp = orch._request("GET", api_path, timeout=10)
        if resp and resp.status_code == 200:
            success("API Authentication Valid (Token/HMAC)")
        else:
            err = orch.describe_last_error()
            failure(f"API Authentication Failed: {err or 'Unknown Error'}")
            if "401" in err or "403" in err:
                info("Suggestion: Check API Token or Gateway Secret.")
    except Exception as e:
        failure(f"API Request Error: {e}")

    # 3. Worker Heartbeat (if possible)
    if server.ssh_key or server.ssh_password:
        try:
            from apps.deployments.services.ssh_client import SSHClient
            ssh = SSHClient(
                ip=server.host,
                key_content=server.ssh_key,
                password=server.ssh_password,
                user=server.ssh_user,
                port=server.ssh_port,
                wg_address=getattr(server, "wg_address", None),
            )
            ssh.connect()
            info("SSH Connection: Valid")
            
            # Check for celery workers
            cmd = "docker ps --filter 'name=celery' --format '{{.Names}}'"
            stdout, stderr, code = ssh.exec_command(cmd)
            if stdout.strip():
                workers = stdout.strip().split('\n')
                success(f"Celery Workers Found: {', '.join(workers)}")
            else:
                warning("No Celery workers found running on remote!")
                
            ssh.close()
        except Exception as e:
            warning(f"SSH Audit Skipped: {e}")
    else:
        info("SSH Audit: No credentials provided.")

    # 4. Check Pending Deployments
    pending = Deployment.objects.filter(
        service__server=server,
        status__in=[Deployment.Status.QUEUED, Deployment.Status.BUILDING]
    ).count()
    if pending > 0:
        warning(f"Node has {pending} deployments in progress/pending.")
    else:
        success("No stalled deployments on this node.")

def main():
    print(f"\n{color('SMSLY GRID ECOSYSTEM AUDITOR', '36;1')}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    servers = ManagedServer.objects.all()
    if not servers:
        print("No managed servers found.")
        return

    print(f"Found {servers.count()} managed nodes.\n")
    
    for server in servers:
        audit_node(server)

    print(f"\n{color('='*60, '36')}")
    print(f"{color('AUDIT COMPLETE', '36;1')}")
    print(f"{color('='*60, '36')}\n")

if __name__ == "__main__":
    main()
