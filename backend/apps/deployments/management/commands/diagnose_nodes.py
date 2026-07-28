"""
diagnose_nodes - Grid inter-node communications diagnostic.

Run on each node to see:
1. What ManagedServer records exist (api_url, status, whether a token is stored)
2. Whether this node's API token/gateway secret is configured
3. A live connectivity test to each remote server

Usage:
    docker compose exec backend python manage.py diagnose_nodes
    docker compose exec backend python manage.py diagnose_nodes --fix
"""

import hashlib
import hmac
import secrets
import time

import requests
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token as DRFToken

from apps.deployments.models.api_token import APIToken
from apps.deployments.models.servers import ManagedServer
from apps.deployments.services.tls_verify import audit_verify, should_verify


class Command(BaseCommand):
    help = "Diagnose Grid inter-node connectivity and authentication issues."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            default=False,
            help="Attempt to auto-fix configuration issues found.",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        self.stdout.write(self.style.MIGRATE_HEADING("\n====== Grid Node Diagnostics ======\n"))

        # --- 1. Local node info ---
        self.stdout.write(self.style.MIGRATE_HEADING("1. LOCAL NODE CREDENTIALS\n"))

        all_tokens = list(APIToken.objects.filter(is_active=True).select_related("user")[:5])
        drf_tokens = list(DRFToken.objects.all().select_related("user")[:5])

        self.stdout.write(f"   smsly_ API tokens in DB: {len(all_tokens)}")
        for t in all_tokens:
            self.stdout.write(f"     • [{t.user.username}] prefix={t.prefix}... name={t.name}")

        self.stdout.write(f"   DRF Token auth tokens in DB: {len(drf_tokens)}")
        for t in drf_tokens:
            self.stdout.write(f"     • [{t.user.username}] key={t.key[:8]}...")

        if fix:
            self._ensure_primary_server()

        if not all_tokens and not drf_tokens:
            self.stdout.write(self.style.ERROR(
                "   ❌ NO API TOKENS EXIST ON THIS NODE! "
                "Remote servers cannot authenticate to this node.\n"
            ))
            if fix:
                self._create_admin_token()

        # --- 2. ManagedServer records ---
        self.stdout.write(self.style.MIGRATE_HEADING("\n2. REGISTERED REMOTE SERVERS\n"))
        servers = list(ManagedServer.objects.all())
        if not servers:
            self.stdout.write("   (no remote servers registered)\n")

        for server in servers:
            has_token = bool(str(server.api_token or "").strip())
            has_secret = bool(str(server.gateway_secret or "").strip())

            # --- Auto-fix Missing Tokens via SSH ---
            if fix and not has_token:
                if server.ssh_key or server.ssh_password:
                    self.stdout.write(f"   Attempting SSH auto-authentication for {server.name}...")
                    from apps.deployments.services.remote_orchestrator import (
                        RemoteOrchestrator,
                    )
                    orch = RemoteOrchestrator(server)
                    if orch.auto_authenticate():
                        self.stdout.write(self.style.SUCCESS("     ✅ Successfully retrieved API token via SSH!"))
                        server.refresh_from_db()
                        has_token = True
                else:
                    self.stdout.write(self.style.WARNING(f"     ⚠️  Cannot auto-fix {server.name}: No SSH credentials stored."))

            token_preview = (str(server.api_token or "")[:12] + "...") if has_token else "MISSING"
            secret_preview = "SET" if has_secret else "MISSING"

            self.stdout.write(f"   Server: {server.name} ({server.host})")
            self.stdout.write(f"     api_url    = {server.api_url or 'NOT SET'}")
            self.stdout.write(f"     api_token  = {token_preview}")
            self.stdout.write(f"     gw_secret  = {secret_preview}")
            self.stdout.write(f"     db_status  = {server.status}")

            if not server.api_url:
                self.stdout.write(self.style.ERROR("     ❌ api_url is BLANK - cannot reach this server!"))
                continue

            # --- 3. Connectivity test ---
            self._test_connectivity(server, has_token, has_secret)
            self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("\n====== Diagnosis Complete ======\n"))
        self.stdout.write("If you see 401/403 errors above, the fix is:\n")
        self.stdout.write("  1. On the TARGET server, run: docker compose exec backend python manage.py diagnose_nodes --fix\n")
        self.stdout.write("     This creates an admin API token.\n")
        self.stdout.write("  2. Alternatively, run this on the SOURCE server: docker compose exec backend python manage.py diagnose_nodes --fix\n")
        self.stdout.write("     It will auto-SSH into remote nodes and pull tokens if credentials exist.\n")

    def _test_connectivity(self, server: ManagedServer, has_token: bool, has_secret: bool):
        from apps.deployments.views.server.helpers import _candidate_api_urls
        candidates = _candidate_api_urls(server)

        # Local-first fallbacks for self-diagnostics
        if server.is_primary or server.host in ("127.0.0.1", "localhost"):
             local_candidates = ["http://localhost:8000", "http://backend:8000", "http://127.0.0.1:8000"]
             for lc in local_candidates:
                 if lc not in candidates:
                     candidates.append(lc)


        base = None

        self.stdout.write(f"     Probing candidates: {', '.join(candidates)}")

        for candidate in candidates:
            try:
                _probe_url = f"{candidate.rstrip('/')}/health"
                _verify = should_verify(_probe_url)
                if not _verify:
                    audit_verify(_probe_url, _verify)
                resp = requests.get(_probe_url, timeout=5, verify=_verify)
                if resp.status_code < 500:
                    base = candidate.rstrip("/")
                    break
                else:
                    self.stdout.write(self.style.WARNING(f"       - {candidate} → HTTP {resp.status_code}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"       - {candidate} → FAILED: {str(e)[:60]}..."))

        self.stdout.write("     Connectivity Audit:")
        from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
        orch = RemoteOrchestrator(server)

        audit = orch.check_connectivity()
        if audit["network"]:
            self.stdout.write(self.style.SUCCESS(f"       ✅ Network Reachable ({audit['latency_ms']}ms)"))
        else:
            self.stdout.write(self.style.ERROR(f"       ❌ Network Unreachable: {audit['error']}"))
            return

        if audit["auth"]:
            self.stdout.write(self.style.SUCCESS("       ✅ API Authentication Valid"))
        else:
            self.stdout.write(self.style.ERROR(f"       ❌ API Authentication Failed: {audit['error']}"))
            if "401" in str(audit['error']) or "403" in str(audit['error']):
                self.stdout.write("          Suggestion: Run --fix on the target or this master to sync tokens.")

        # Update server if primary candidate works
        base = audit.get("base_url") or orch._candidate_base_urls()[0]
        if server.api_url != base:
            self.stdout.write(f"        (Updating api_url from {server.api_url} to {base})")
            server.api_url = base
            server.save(update_fields=["api_url"])

        # Step B: Authenticated API call

        api_path = "/api/v1/services/"
        url = f"{base}{api_path}"
        headers = {"Accept": "application/json"}

        if has_token:
            token = str(server.api_token).strip()
            if token.startswith("smsly_"):
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = f"Token {token}"

            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    self.stdout.write(self.style.SUCCESS(
                        f"     ✅ GET {api_path} → HTTP 200 (TOKEN AUTH WORKS)"
                    ))
                    try:
                        data = resp.json()
                        count = data.get("count", len(data.get("results", data if isinstance(data, list) else [])))
                        self.stdout.write(f"        Services visible: {count}")
                    except (ValueError, KeyError) as exc:
                        self.stdout.write(f"        (Could not parse response: {exc})")
                else:
                    self.stdout.write(self.style.ERROR(
                        f"     ❌ GET {api_path} → HTTP {resp.status_code} with TOKEN auth"
                    ))
                    self.stdout.write(f"        Response: {resp.text[:300]}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"     ❌ API call FAILED: {e}"))

        elif has_secret:
            # Try HMAC auth
            gw_secret = str(server.gateway_secret).strip()
            ts = str(int(time.time()))
            nonce = secrets.token_urlsafe(16)
            body_hash = hashlib.sha256(b"").hexdigest()
            # SECURITY (Batch G): nonce is mandatory and bound into
            # the signed payload. Matches ZeroTrustHMACAuthentication.
            payload_str = f"GET|{api_path}|{ts}|{nonce}|{body_hash}"
            sig = hmac.new(gw_secret.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
            headers["X-SMSLY-Remote-Sync"] = "1"
            headers["X-Gateway-Signature-V2"] = sig
            headers["X-Request-Timestamp"] = ts
            headers["X-Request-Nonce"] = nonce

            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    self.stdout.write(self.style.SUCCESS(
                        f"     ✅ GET {api_path} → HTTP 200 (HMAC AUTH WORKS)"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(
                        f"     ❌ GET {api_path} → HTTP {resp.status_code} with HMAC auth"
                    ))
                    self.stdout.write(f"        Response: {resp.text[:300]}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"     ❌ HMAC API call FAILED: {e}"))
        else:
            self.stdout.write(self.style.WARNING(
                "     ⚠️  No api_token or gateway_secret stored - cannot authenticate!"
            ))

    def _ensure_primary_server(self):
        """Ensure a primary ManagedServer record exists for the local machine."""
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()

        primary = ManagedServer.get_primary()
        if primary:
            self.stdout.write(f"   ✅ Primary server already exists: {primary.name} ({primary.host})")
            return

        admin = APIToken.objects.filter(user__is_superuser=True).first()
        if not admin:
            from django.contrib.auth import get_user_model
            admin_user = get_user_model().objects.filter(is_superuser=True).first()
        else:
            admin_user = admin.user

        if not admin_user:
            self.stdout.write(self.style.ERROR("   ❌ Cannot create primary server: No superuser found."))
            return

        host = config.server_ip or "127.0.0.1"

        # Primary node should use port 8090 (Nginx) or 80/443 (Traefik/Caddy)
        # when accessed from outside. Port 8000 is internal.
        if host in ("localhost", "127.0.0.1"):
            api_url = f"http://{host}:8000"
        else:
            # Use domain if available, fallback to IP:8090
            domain = config.domain
            if domain and domain not in ("localhost", "127.0.0.1"):
                scheme = "https" if config.use_ssl else "http"
                api_url = f"{scheme}://{domain}"
            else:
                api_url = f"http://{host}:8090"

        ManagedServer.objects.create(
            owner=admin_user,
            name="Master Node (Auto-generated)",
            host=host,
            api_url=api_url,

            is_primary=True,
            status=ManagedServer.Status.ONLINE,
            provision_status=ManagedServer.ProvisionStatus.DONE,
        )
        self.stdout.write(self.style.SUCCESS(f"   ✅ Created primary server record for {host}"))

    def _create_admin_token(self):
        """Create an API token for the admin user."""
        from django.contrib.auth import get_user_model
        User = get_user_model()

        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stdout.write(self.style.ERROR("   No superuser found — cannot auto-create token."))
            return

        _token_instance, raw_token = APIToken.create_token(admin, name="Inter-Node Access")
        self.stdout.write(self.style.SUCCESS(
            f"\n   ✅ Created API token for [{admin.username}]:\n"
            f"   TOKEN: {raw_token}\n\n"
            f"   Copy this token and paste it into the SOURCE server's ManagedServer 'API Token' field.\n"
        ))
