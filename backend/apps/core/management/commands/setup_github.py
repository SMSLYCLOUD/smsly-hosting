"""Management command to setup GitHub integration.

Validates credentials, creates SocialApp for OAuth, and prints
setup instructions for the GitHub App.

Usage:
    # Interactive — prompts for missing values:
    python manage.py setup_github

    # With all values:
    python manage.py setup_github \
        --app-id 123456 \
        --app-private-key @/path/to/key.pem \
        --oauth-client-id Iv1.abc123 \
        --oauth-client-secret secret123

    # Validate only (no changes):
    python manage.py setup_github --check
"""

import os
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Setup GitHub App + OAuth integration"

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Validate only, don't write anything")
        parser.add_argument("--app-id", type=str, help="GitHub App numeric ID")
        parser.add_argument("--app-private-key", type=str, help="Path to .pem file or inline PEM key")
        parser.add_argument("--oauth-client-id", type=str, help="GitHub OAuth App client ID")
        parser.add_argument("--oauth-client-secret", type=str, help="GitHub OAuth App client secret")
        parser.add_argument("--webhook-secret", type=str, help="Webhook signature secret (auto-generated if omitted)")

    def handle(self, *args, **options):
        check_only = options["check"]

        self.stdout.write(self.style.HTTP_INFO("=" * 60))
        self.stdout.write(self.style.HTTP_INFO("  GitHub Integration Setup"))
        self.stdout.write(self.style.HTTP_INFO("=" * 60))

        # ── Step 1: Gather credentials ────────────────────────────────
        app_id = options["app_id"] or os.environ.get("GITHUB_APP_ID", "")
        app_key_path = options["app_private_key"] or os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
        oauth_id = options["oauth_client_id"] or os.environ.get("GITHUB_CLIENT_ID", "")
        oauth_secret = options["oauth_client_secret"] or os.environ.get("GITHUB_CLIENT_SECRET", "")
        webhook_secret = options["webhook_secret"] or os.environ.get("GITHUB_WEBHOOK_SECRET", "")

        # Interactive prompts for missing values
        if not app_id and not check_only:
            app_id = input("GitHub App ID (from settings page): ").strip()
        if not app_key_path and not check_only:
            app_key_path = input("Path to GitHub App .pem file: ").strip()
        if not oauth_id and not check_only:
            oauth_id = input("GitHub OAuth Client ID (from OAuth app settings): ").strip()
        if not oauth_secret and not check_only:
            oauth_secret = input("GitHub OAuth Client Secret: ").strip()

        # ── Step 2: Read private key from file if path ────────────────
        private_key = ""
        if app_key_path:
            if app_key_path.startswith("-----BEGIN"):
                private_key = app_key_path
            elif os.path.isfile(app_key_path):
                with open(app_key_path) as f:
                    private_key = f.read().strip()
            else:
                self.stdout.write(self.style.ERROR(f"File not found: {app_key_path}"))
                return

        # ── Step 3: Validate ──────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("Validation:"))
        ok = True

        if app_id:
            self.stdout.write(self.style.SUCCESS(f"  [OK] GitHub App ID: {app_id}"))
        else:
            self.stdout.write(self.style.WARNING("  [--] GitHub App ID: not set (GitHub App features disabled)"))
            ok = False

        if private_key:
            if private_key.startswith("-----BEGIN") and "PRIVATE KEY" in private_key:
                self.stdout.write(self.style.SUCCESS("  [OK] GitHub App Private Key: valid PEM format"))
            else:
                self.stdout.write(self.style.ERROR("  [FAIL] GitHub App Private Key: not a valid PEM key"))
                ok = False
        else:
            self.stdout.write(self.style.WARNING("  [--] GitHub App Private Key: not set"))
            ok = False

        if oauth_id:
            self.stdout.write(self.style.SUCCESS(f"  [OK] OAuth Client ID: {oauth_id[:8]}..."))
        else:
            self.stdout.write(self.style.WARNING("  [--] OAuth Client ID: not set (user login disabled)"))

        if oauth_secret:
            self.stdout.write(self.style.SUCCESS(f"  [OK] OAuth Client Secret: ***{oauth_secret[-4:]}"))
        else:
            self.stdout.write(self.style.WARNING("  [--] OAuth Client Secret: not set"))

        if webhook_secret:
            self.stdout.write(self.style.SUCCESS(f"  [OK] Webhook Secret: ***{webhook_secret[-4:]}"))
        else:
            self.stdout.write(self.style.WARNING("  [--] Webhook Secret: not set (webhooks will be rejected)"))

        if check_only:
            self.stdout.write("")
            if ok:
                self.stdout.write(self.style.SUCCESS("All required credentials are set."))
            else:
                self.stdout.write(self.style.WARNING("Some credentials are missing. See instructions below."))
            self._print_instructions()
            return

        # ── Step 4: Write .env file ───────────────────────────────────
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            ".env",
        )

        # Generate webhook secret if not provided
        if not webhook_secret:
            import secrets
            webhook_secret = secrets.token_hex(32)
            self.stdout.write(f"\n  Generated webhook secret: {webhook_secret[:8]}...{webhook_secret[-8:]}")

        env_updates = {}
        if app_id:
            env_updates["GITHUB_APP_ID"] = app_id
        if private_key:
            # Escape newlines for .env file
            env_updates["GITHUB_APP_PRIVATE_KEY"] = private_key.replace("\n", "\\n")
        if oauth_id:
            env_updates["GITHUB_CLIENT_ID"] = oauth_id
        if oauth_secret:
            env_updates["GITHUB_CLIENT_SECRET"] = oauth_secret
        if webhook_secret:
            env_updates["GITHUB_WEBHOOK_SECRET"] = webhook_secret

        if env_updates:
            self._update_env(env_path, env_updates)
            self.stdout.write(self.style.SUCCESS(f"\nUpdated {env_path}"))
        else:
            self.stdout.write(self.style.WARNING("\nNo credentials to write."))

        # ── Step 5: Create SocialApp for OAuth ────────────────────────
        if oauth_id and oauth_secret:
            self._create_social_app(oauth_id, oauth_secret)

        # ── Step 6: Print next steps ──────────────────────────────────
        self._print_instructions()

    def _update_env(self, env_path: str, updates: dict):
        """Update key=value pairs in .env file."""
        lines = []
        if os.path.isfile(env_path):
            with open(env_path) as f:
                lines = f.readlines()

        updated_keys = set()
        new_lines = []
        for line in lines:
            key = line.split("=")[0].strip() if "=" in line else ""
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line)

        for key, val in updates.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={val}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)

    def _create_social_app(self, client_id: str, secret: str):
        """Create or update the GitHub SocialApp for OAuth login."""
        try:
            from allauth.socialaccount.models import SocialApp
            from django.contrib.sites.models import Site
            from django.conf import settings

            site = Site.objects.get(id=settings.SITE_ID)
            app, created = SocialApp.objects.update_or_create(
                provider="github",
                defaults={
                    "name": "GitHub",
                    "client_id": client_id,
                    "secret": secret,
                },
            )
            app.sites.add(site)
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"\n{verb} GitHub SocialApp for OAuth login"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not create SocialApp (run manually): {e}"))

    def _print_instructions(self):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=" * 60))
        self.stdout.write(self.style.HTTP_INFO("  SETUP INSTRUCTIONS"))
        self.stdout.write(self.style.HTTP_INFO("=" * 60))
        self.stdout.write("""
You need to create TWO things on GitHub:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 1. GITHUB APP (for server-to-server: clone repos, commit statuses)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Go to: https://github.com/organizations/{org}/settings/apps/new
     (replace {org} with your GitHub organization)

  2. Fill in:
     • GitHub App name: smsly-paas-builder
     • Homepage URL: https://{domain}
     • Webhook URL: https://{domain}/webhooks/github/
     • Webhook secret: (use the value from your .env file)

  3. Permissions → Repository permissions:
     • Contents: Read-only      (clone private repos)
     • Commit statuses: Read & Write (post deploy status)
     • Pull requests: Read & Write   (comment on PRs)
     • Deployments: Read & Write     (create deployments)

  4. Subscribe to events:
     • Push
     • Pull request
     • Installation
     • Installation repositories

  5. Where can this GitHub App be installed:
     • Only on this account

  6. Click "Create GitHub App"

  7. On the App settings page:
     • Note the "App ID" (numeric)
     • Scroll to "Private keys" → click "Generate a private key"
     • A .pem file will download — keep it safe!

  8. Install the App on your org:
     • Go to: https://github.com/apps/smsly-paas-builder/installations/new
     • Select repos to grant access to

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 2. GITHUB OAUTH APP (for user login: "Sign in with GitHub")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Go to: https://github.com/settings/developers
  2. Click "New OAuth App"
  3. Fill in:
     • Application name: SMSLY Grid
     • Homepage URL: https://{domain}
     • Authorization callback URL: https://{domain}/auth/github/callback
  4. Click "Register application"
  5. Note the "Client ID"
  6. Click "Generate a new client secret" — copy it immediately!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AFTER CREATING BOTH:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Run this command with your credentials:

    python manage.py setup_github \\
        --app-id YOUR_APP_ID \\
        --app-private-key /path/to/key.pem \\
        --oauth-client-id YOUR_CLIENT_ID \\
        --oauth-client-secret YOUR_SECRET

  Then restart the backend:

    docker compose up -d --build --no-deps backend
""")
