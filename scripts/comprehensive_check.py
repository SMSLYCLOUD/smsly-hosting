#!/usr/bin/env python
import os
import sys

import django

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Setup Django
django.setup()

print("=== Custom Domain System Status Report ===\n")

# 1. Check Domain and Service Records
print("1. Database Records Check")
print("-" * 40)

from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus

# Check Domain records
domains = Domain.objects.all()
print(f"Total Domain records: {domains.count()}")
if domains.exists():
    for domain in domains:
        service = domain.service
        print(f"  Domain ID: {domain.id}")
        print(f"    Name: {domain.domain_name}")
        print(f"    Status: {domain.status}")
        print(f"    SSL Active: {domain.ssl_active}")
        print(f"    Verified: {domain.verified}")
        print(f"    Service: {service.name if service else 'None'} (ID: {service.id if service else 'None'})")
        print(f"    DNS Expected: {domain.dns_expected}")
        print(f"    DNS Actual: {domain.dns_actual}")
        print(f"    Last Error: {domain.last_error}")
        print(f"    Created: {domain.created_at}")
        print(f"    Updated: {domain.updated_at}")
        print()
else:
    print("  No Domain records found")

# Check Service records
services = Service.objects.all()
print(f"Total Service records: {services.count()}")
if services.exists():
    for service in services:
        print(f"  Service ID: {service.id}")
        print(f"    Name: {service.name}")
        print(f"    Status: {service.status}")
        print(f"    Public Domain: {service.public_domain}")
        print(f"    Owner: {service.owner.username if service.owner else 'None'}")

        # Check if service has custom_domains field (old system)
        if hasattr(service, 'custom_domains'):
            custom_domains = getattr(service, 'custom_domains', None)
            if custom_domains:
                print(f"    Custom Domains (old field): {custom_domains}")
        print()
else:
    print("  No Service records found")

# 2. Check Platform Configuration
print("2. Platform Configuration")
print("-" * 40)

from apps.deployments.models import PlatformConfig

try:
    config = PlatformConfig.load()
    print(f"Platform Domain: {config.domain}")
    print(f"Use SSL: {config.use_ssl}")
    print(f"Wildcard Subdomains: {config.wildcard_subdomains}")
    print(f"Server IP: {getattr(config, 'server_ip', 'Not set')}")
    print(f"Cloudflare Token: {'Set' if getattr(config, 'cloudflare_api_token', '') else 'Not set'}")
except Exception as e:
    print(f"Error loading PlatformConfig: {e}")

print()

# 3. Check DNS Verification Logic
print("3. DNS Verification Test")
print("-" * 40)

from apps.domains.verification import verify_custom_domain_dns

# Test with existing domains
if domains.exists():
    for domain in domains:
        print(f"Testing domain: {domain.domain_name}")
        result = verify_custom_domain_dns(domain, config)
        print(f"  Verified: {result.verified}")
        print(f"  Expected: {result.expected}")
        print(f"  Actual: {result.actual}")
        print(f"  Matched by: {result.matched_by}")
        print(f"  Error: {result.error}")
        print()

        # Check if domain should be updated
        if result.verified and domain.status != DomainStatus.DNS_VERIFIED:
            print(f"  -> DNS VERIFIED but status is {domain.status} - needs update")
        elif not result.verified and domain.status in [DomainStatus.DNS_VERIFIED, DomainStatus.ACTIVE]:
            print(f"  -> DNS NOT VERIFIED but status is {domain.status} - potential issue")

# Test with platform domain
print("Testing platform domain (grid.smsly.cloud):")
class MockDomain:
    def __init__(self, domain_name):
        self.domain_name = domain_name
        self.service = None

mock_domain = MockDomain('grid.smsly.cloud')
result = verify_custom_domain_dns(mock_domain, config)
print(f"  Verified: {result.verified}")
print(f"  Expected: {result.expected}")
print(f"  Actual: {result.actual}")
print(f"  Matched by: {result.matched_by}")
print(f"  Error: {result.error}")
print()

# 4. Check Celery Task Configuration
print("4. Celery Task Configuration")
print("-" * 40)

try:
    from apps.domains.tasks import verify_dns_and_provision_ssl_task
    from config.celery import app

    print(f"Domain Task Name: {verify_dns_and_provision_ssl_task.name}")
    print(f"Task Registered: {verify_dns_and_provision_ssl_task in app.tasks}")

    # Check if task is in beat schedule
    beat_schedule = app.conf.beat_schedule
    ssl_tasks = {k: v for k, v in beat_schedule.items() if 'ssl' in k.lower() or 'domain' in k.lower()}
    if ssl_tasks:
        print("SSL/Domain tasks in beat schedule:")
        for name, config in ssl_tasks.items():
            print(f"  {name}: {config}")
    else:
        print("No SSL/Domain tasks found in beat schedule")

except Exception as e:
    print(f"Error checking Celery tasks: {e}")

print()

# 5. Check Domain Task Execution
print("5. Domain Task Execution Test")
print("-" * 40)

if domains.exists():
    domain = domains.first()
    print(f"Testing task execution for domain: {domain.domain_name}")

    try:
        # Test synchronous task execution
        from apps.domains.tasks import verify_dns_and_provision_ssl_task

        # Get current status
        old_status = domain.status
        old_verified = domain.verified
        print(f"  Initial status: {old_status}")
        print(f"  Initial verified: {old_verified}")

        # Execute task
        result = verify_dns_and_provision_ssl_task(domain.id)
        print(f"  Task result: {result}")

        # Check updated status
        domain.refresh_from_db()
        print(f"  New status: {domain.status}")
        print(f"  New verified: {domain.verified}")
        print(f"  Last error: {domain.last_error}")

        if domain.status != old_status:
            print(f"  -> Status changed from {old_status} to {domain.status}")
        else:
            print("  -> Status unchanged - task may not have run properly")

    except Exception as e:
        print(f"Error executing domain task: {e}")
else:
    print("  No domains to test with")

print()

# 6. Summary
print("6. System Summary")
print("-" * 40)

issues = []
if not domains.exists():
    issues.append("No domain records found - system may not be processing domains")
elif domains.filter(status='pending').count() > 0:
    issues.append(f"{domains.filter(status='pending').count()} domains stuck in 'pending' status")

if not services.exists():
    issues.append("No service records found")

if not getattr(config, 'domain', ''):
    issues.append("No platform domain configured")

try:
    from config.celery import app
    if not any('domain' in task_name for task_name in app.tasks):
        issues.append("Domain tasks not properly registered")
except Exception as e:
    issues.append(f"Celery configuration issue: {e}")

if issues:
    print("Issues found:")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")
else:
    print("No obvious issues detected")

print("\n=== End Status Report ===")
