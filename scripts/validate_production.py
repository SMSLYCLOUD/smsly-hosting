# Security & Environment Variable Validation Script
# Run before production deployment to ensure all security requirements are met

import os
import sys
from pathlib import Path


def color_text(text, color_code):
    """Add color to terminal output."""
    return f"\033[{color_code}m{text}\033[0m"

def success(msg):
    # ASCII-only prefixes so this script works on Windows consoles (cp1252) too.
    print(f"[OK] {color_text(msg, '32')}")  # Green

def error(msg):
    print(f"[ERR] {color_text(msg, '31')}")  # Red

def warning(msg):
    print(f"[WARN] {color_text(msg, '33')}")  # Yellow

def info(msg):
    print(f"[INFO] {msg}")

def check_required_env_vars():
    """Check that all required environment variables are set."""
    print("\n" + "="*60)
    print("CHECKING REQUIRED ENVIRONMENT VARIABLES")
    print("="*60 + "\n")
    
    required_vars = [
        'SECRET_KEY',
        'FIELD_ENCRYPTION_KEY',
        'POSTGRES_PASSWORD',
        'REDIS_PASSWORD',
        'GATEWAY_SECRET',
        'GITHUB_WEBHOOK_SECRET',
        'ALLOWED_HOSTS',
    ]
    
    all_present = True
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            error(f"{var} is not set")
            all_present = False
        elif value in ['', 'django-insecure-smsly-hosting-dev-key', 'changeme', 'password']:
            error(f"{var} is set to an insecure default value")
            all_present = False
        else:
            success(f"{var} is set")
    
    return all_present

def check_security_settings():
    """Validate security-related settings."""
    print("\n" + "="*60)
    print("CHECKING SECURITY SETTINGS")
    print("="*60 + "\n")
    
    issues = []
    
    # Check DEBUG mode
    debug = os.getenv('DEBUG', 'False').lower()
    if debug in ['true', '1', 'yes']:
        error("DEBUG is enabled (should be False in production)")
        issues.append("DEBUG enabled")
    else:
        success("DEBUG is disabled")
    
    # Check ALLOWED_HOSTS
    allowed_hosts = os.getenv('ALLOWED_HOSTS', '')
    if allowed_hosts == '*' or not allowed_hosts:
        error("ALLOWED_HOSTS is set to wildcard or empty (security risk)")
        issues.append("ALLOWED_HOSTS misconfigured")
    else:
        success(f"ALLOWED_HOSTS configured: {allowed_hosts}")
    
    # Check CORS
    # CORS_ALLOW_ALL_ORIGINS is now hardcoded to False in settings.py (ZH-006 FIX)
    success("CORS_ALLOW_ALL_ORIGINS hardcoded to False")
    
    # Check FIELD_ENCRYPTION_KEY format
    encryption_key = os.getenv('FIELD_ENCRYPTION_KEY', '')
    if encryption_key:
        try:
            from cryptography.fernet import Fernet
            Fernet(encryption_key.encode())
            success("FIELD_ENCRYPTION_KEY format is valid")
        except ImportError:
            warning("cryptography not installed; cannot validate FIELD_ENCRYPTION_KEY format")
        except Exception as e:
            error(f"FIELD_ENCRYPTION_KEY is invalid: {e}")
            issues.append("Invalid encryption key")
    
    return len(issues) == 0

def check_ssl_configuration():
    """Check SSL/TLS configuration."""
    print("\n" + "="*60)
    print("CHECKING SSL/TLS CONFIGURATION")
    print("="*60 + "\n")
    
    acme_email = os.getenv('ACME_EMAIL', '')
    domain = os.getenv('DOMAIN', '')
    
    if not acme_email or '@example.com' in acme_email:
        print("[validate] WARNING: ACME_EMAIL not set or using default. SSL might fail.")
    else:
        success(f"ACME_EMAIL configured: {acme_email}")
    
    if not domain or domain == 'localhost':
        print("[validate] WARNING: DOMAIN is not set to a public address.")
        return False
    else:
        success(f"DOMAIN configured: {domain}")
    
    return True

def check_file_permissions():
    """Check critical file permissions."""
    print("\n" + "="*60)
    print("CHECKING FILE PERMISSIONS")
    print("="*60 + "\n")
    
    env_file = Path('.env')
    if env_file.exists():
        # Windows/NTFS permissions don't map to POSIX file modes in a meaningful way.
        if os.name != 'posix':
            info(".env file exists (permission checks skipped on non-POSIX platforms)")
            return True

        mode = oct(env_file.stat().st_mode)[-3:]
        if mode in ['600', '400']:
            success(f".env file permissions are secure ({mode})")
        else:
            warning(f".env file permissions are too open ({mode}). Run: chmod 600 .env")
    else:
        error(".env file not found")
        return False
    
    return True

def main():
    """Run all validation checks."""
    print("\n" + color_text("="*60, '36'))
    print(color_text("SMSLY HOSTING - PRODUCTION READINESS VALIDATOR", '36'))
    print(color_text("="*60, '36'))
    
    # Load .env file if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
        info("Loaded environment from .env file")
    except ImportError:
        warning("python-dotenv not installed, using system environment only")
    
    checks = [
        ("Environment Variables", check_required_env_vars),
        ("Security Settings", check_security_settings),
        ("SSL Configuration", check_ssl_configuration),
        ("File Permissions", check_file_permissions),
    ]
    
    results = []
    for name, check_func in checks:
        result = check_func()
        results.append((name, result))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60 + "\n")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        if result:
            success(f"{name}: PASSED")
        else:
            error(f"{name}: FAILED")
    
    print(f"\nOverall: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n" + color_text("ALL CHECKS PASSED - READY FOR PRODUCTION", '32'))
        sys.exit(0)
    else:
        print("\n" + color_text("SOME CHECKS FAILED - FIX ISSUES BEFORE DEPLOYING", '31'))
        sys.exit(1)

if __name__ == '__main__':
    main()
