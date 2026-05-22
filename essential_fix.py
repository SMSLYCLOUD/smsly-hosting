#!/usr/bin/env python3
"""
Essential Custom Domain SSL Fix
Runs the critical fix steps to get the system working
"""

import os
import sys
import subprocess
import time
from pathlib import Path

def run_command(cmd, description):
    """Run a command and handle errors"""
    print(f"Running: {description}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print(f"Success: {description}")
            if result.stdout:
                print(f"Output: {result.stdout}")
            return True
        else:
            print(f"Failed: {description}")
            print(f"Error: {result.stderr}")
            return False
    except Exception as e:
        print(f"Error: {description} - {e}")
        return False

def main():
    print("ESSENTIAL Custom Domain SSL Fix")
    print("=" * 50)
    
    # Step 1: Install dependencies
    print("\n1. Installing required dependencies...")
    dependencies = ["dj_database_url", "celery", "redis", "python-dotenv"]
    
    for dep in dependencies:
        if not run_command(f"pip install {dep}", f"Install {dep}"):
            print(f"Failed to install {dep}")
            return False
    
    # Step 2: Check and start Celery worker
    print("\n2. Starting Celery worker...")
    os.chdir("backend")
    
    # Test Celery configuration
    if not run_command("celery -A config worker --loglevel=info", "Test Celery worker"):
        print("Celery configuration failed")
        print("Make sure you're in the backend directory and config module exists")
        return False
    
    # Step 3: Run domain verification
    print("\n3. Running domain verification tasks...")
    
    verification_script = '''
import os
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from apps.domains.models import Domain
from apps.domains.tasks import verify_dns_and_provision_ssl_task

print("Checking domain statuses...")
domains = Domain.objects.all()
for domain in domains:
    print(f"Domain: {domain.domain_name}, Status: {domain.status}, Verified: {domain.verified}, SSL: {domain.ssl_active}")

print("\\nProcessing pending domains...")
pending_domains = Domain.objects.filter(status__in=["pending", "dns_pending"])
print(f"Found {pending_domains.count()} pending domains")

for domain in pending_domains:
    print(f"\\nProcessing: {domain.domain_name}")
    verify_dns_and_provision_ssl_task(domain.id)
    print(f"Task queued for {domain.domain_name}")
'''
    
    if not run_command(f'python -c "{verification_script}"', "Run domain verification"):
        print("Domain verification failed")
        return False
    
    # Step 4: Check final status
    print("\n4. Checking final domain status...")
    
    status_script = '''
import os
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from apps.domains.models import Domain

print("\\n=== FINAL DOMAIN STATUS ===")
for domain in Domain.objects.all():
    print(f"{domain.domain_name}:")
    print(f"  Status: {domain.status}")
    print(f"  Verified: {domain.verified}")  
    print(f"  SSL Active: {domain.ssl_active}")
    print(f"  DNS Expected: {domain.dns_expected}")
    print(f"  DNS Actual: {domain.dns_actual}")
    print(f"  Last Error: {domain.last_error}")
    print()
'''
    
    if not run_command(f'python -c "{status_script}"', "Check domain status"):
        print("Status check failed")
        return False
    
    print("\nEssential fix completed!")
    print("\nNext Steps:")
    print("1. Start Docker Desktop (if not running)")
    print("2. Keep Celery worker running: cd backend && celery -A config worker --loglevel=info")
    print("3. Add a custom domain that points to your VPS IP (209.159.152.123)")
    print("4. Monitor domain status changes")
    print("5. Test HTTPS access to the custom domain")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)