#!/usr/bin/env python3
"""
Generate all required .env secrets for SMSLY Hosting.

Usage:
    python scripts/generate_env_secrets.py          # KEY=value lines to stdout
    python scripts/generate_env_secrets.py --env     # append to .env
    python scripts/generate_env_secrets.py --env .env.production

All secrets except FIELD_ENCRYPTION_KEY are generated using Python stdlib only.
FIELD_ENCRYPTION_KEY requires the 'cryptography' package; if missing,
a placeholder instruction is printed instead.
"""

import argparse
import secrets
import string
import sys


# Lazy import: cryptography is only needed for the Fernet key.
# If not installed, we generate all other secrets and print a clear instruction.
def _generate_fernet_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return ""
    return Fernet.generate_key().decode()


SECRET_DEFINITIONS = [
    ("SECRET_KEY", 50, "Django secret key for cryptographic signing"),
    ("FIELD_ENCRYPTION_KEY", None, "Fernet key for encrypting model fields at rest"),
    ("POSTGRES_PASSWORD", 32, "PostgreSQL database password"),
    ("REDIS_PASSWORD", 32, "Redis authentication password"),
    ("RABBITMQ_PASSWORD", 32, "RabbitMQ message broker password"),
    ("GATEWAY_SECRET", 64, "Inter-service HMAC authentication secret"),
    ("GITHUB_WEBHOOK_SECRET", 64, "GitHub webhook signature verification"),
    ("AUTOSCALER_API_TOKEN", 64, "Autoscaler API bearer token"),
    ("FRP_AUTH_TOKEN", 64, "FRP tunnel relay authentication token"),
    ("PGCAT_ADMIN_PASSWORD", 48, "PgCat administration password"),
    ("REPLICATION_PASSWORD", 32, "PostgreSQL streaming replication password"),
    ("SENTINEL_PASSWORD", 32, "Redis Sentinel authentication password"),
    ("REGISTRY_HTTP_SECRET", 32, "Docker registry internal secret"),
    ("CROWDSEC_BOUNCER_KEY", 32, "CrowdSec bouncer key for Traefik"),
    ("COSIGN_PASSWORD", 32, "Password protecting the Cosign private key"),
]


def generate_secret_key(length: int = 50) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def generate_fernet_key() -> str:
    key = _generate_fernet_key()
    if not key:
        # Print a clear instruction that will be visible in the script output
        print("FIELD_ENCRYPTION_KEY=__INSTALL_CRYPTOGRAPHY__", file=sys.stderr)
        print("# Install cryptography and re-run to get FIELD_ENCRYPTION_KEY", file=sys.stderr)
        return "__INSTALL_CRYPTOGRAPHY__"
    return key


def generate_hex_secret(bytes_count: int) -> str:
    return secrets.token_hex(bytes_count)


def generate_all() -> dict[str, str]:
    result = {}
    for name, length_or_none, _desc in SECRET_DEFINITIONS:
        if name == "FIELD_ENCRYPTION_KEY":
            result[name] = generate_fernet_key()
        elif name == "SECRET_KEY":
            result[name] = generate_secret_key(length_or_none)
        else:
            result[name] = generate_hex_secret(length_or_none)
    return result


def print_secrets(secrets_dict: dict[str, str]) -> None:
    max_name_len = max(len(k) for k in secrets_dict)
    print("=" * 70)
    print("  SMSLY HOSTING — REQUIRED SECRETS")
    print("=" * 70)
    for name, _length, desc in SECRET_DEFINITIONS:
        print(f"\n  # {desc}")
        print(f"  {name:<{max_name_len}} = {secrets_dict[name]}")
    print()
    print("  Copy these into your .env file and keep them SECURE.")
    print("  Run with --env to append directly to .env.")
    print("=" * 70)


def print_shell(secrets_dict: dict[str, str]) -> None:
    """Print secrets as KEY=VALUE lines for shell consumption."""
    for name, _length, _desc in SECRET_DEFINITIONS:
        print(f"{name}={secrets_dict[name]}")


def append_to_env(env_path: str, secrets_dict: dict[str, str], dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] Would append secrets to: {env_path}")
        return

    try:
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"\n# Auto-generated secrets ({__file__})\n")
            for name, _length, _desc in SECRET_DEFINITIONS:
                f.write(f"{name}={secrets_dict[name]}\n")
        print(f"Secrets appended to {env_path}")
    except OSError as e:
        print(f"ERROR: Could not write to {env_path}: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate .env secrets for SMSLY Hosting")
    parser.add_argument(
        "--env",
        nargs="?",
        const=".env",
        default=None,
        help="Append secrets to .env file (default: print to stdout)",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Output as KEY=VALUE lines for shell consumption",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without modifying files",
    )
    args = parser.parse_args()

    secrets_dict = generate_all()

    if args.env:
        append_to_env(args.env, secrets_dict, dry_run=args.dry_run)
    elif args.shell:
        print_shell(secrets_dict)
    else:
        print_secrets(secrets_dict)


if __name__ == "__main__":
    main()
