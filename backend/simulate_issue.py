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

from django.contrib.auth import get_user_model
from apps.deployments.models import Service, PlatformConfig
from apps.domains.models import Domain, DomainStatus
from apps.domains.tasks import verify_dns_and_provision_ssl_task
from apps.domains.verification import verify_custom_domain_dns
from apps.deployments.domain_utils import normalize_domain

User = get_user_model()

print('=== Creating Test Data to Simulate Issue ===')

# Create a test user if none exists
users = User.objects.all()
if not users.exists():
    print('Creating test user...')
    user = User.objects.create_user(
        username='testuser',
        email='test@example.com',
        password='testpass123'
    )
else:
    user = users.first()
    print(f'Using existing user: {user.username}')

# Create a test service if none exists
services = Service.objects.all()
if not services.exists():
    print('Creating test service...')
    service = Service.objects.create(
        name='test-service',
        owner=user,
        public_domain='test-service.grid.smsly.cloud',
        status='ACTIVE'
    )
else:
    service = services.first()
    print(f'Using existing service: {service.name}')

print(f'Service created: {service.name} (ID: {service.id})')

# Test DNS verification with a domain that should work (pointing to the platform)
test_domain = 'working-test.example.com'
print(f'\n=== Testing DNS verification for domain: {test_domain} ===')

# Create test domain
domain = Domain.objects.create(
    domain_name=test_domain,
    service=service,
    status=DomainStatus.PENDING
)
print(f'Created domain: {domain.domain_name} (ID: {domain.id})')

# Load config
config = PlatformConfig.load()

# Test DNS verification
print('Running DNS verification...')
result = verify_custom_domain_dns(domain, config)
print(f'Verified: {result.verified}')
print(f'Expected: {result.expected}')
print(f'Actual: {result.actual}')
print(f'Matched by: {result.matched_by}')
print(f'Error: {result.error}')

if result.verified:
    print('DNS verification passed - domain points to platform')
    print('Testing domain status update...')
    domain.status = DomainStatus.DNS_VERIFIED
    domain.verified = True
    domain.save(update_fields=['status', 'verified'])
    print(f'Domain status updated to: {domain.status}')
else:
    print('DNS verification failed - domain does not point to platform')
    print('This is expected for a test domain that does not actually exist in DNS')

# Test the Celery task
print(f'\n=== Testing Celery task for domain: {domain.id} ===')
try:
    # This should work but won't actually trigger Caddy reload in this environment
    verify_dns_and_provision_ssl_task(domain.id)
    domain.refresh_from_db()
    print(f'Task executed. Domain status: {domain.status}')
    print(f'Domain verified: {domain.verified}')
    print(f'Last error: {domain.last_error}')
except Exception as e:
    print(f'Error executing Celery task: {e}')

print(f'\n=== Current Domain Status ===')
print(f'Domain: {domain.domain_name}')
print(f'Status: {domain.status}')
print(f'Verified: {domain.verified}')
print(f'SSL Active: {domain.ssl_active}')
print(f'DNS Expected: {domain.dns_expected}')
print(f'DNS Actual: {domain.dns_actual}')
print(f'Last Error: {domain.last_error}')

print(f'\n=== Testing with a domain that actually points to the platform ===')
# Test with the actual platform domain
platform_domain = 'grid.smsly.cloud'
print(f'Testing domain: {platform_domain}')

platform_domain_obj = Domain.objects.create(
    domain_name=platform_domain,
    service=service,
    status=DomainStatus.PENDING
)

result = verify_custom_domain_dns(platform_domain_obj, config)
print(f'Verified: {result.verified}')
print(f'Expected: {result.expected}')
print(f'Actual: {result.actual}')
print(f'Matched by: {result.matched_by}')
print(f'Error: {result.error}')

# Clean up
print(f'\n=== Cleaning up test data ===')
domain.delete()
platform_domain_obj.delete()
print('Test data cleaned up')