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

from apps.deployments.models import PlatformConfig, Service
from apps.domains.models import Domain, DomainStatus
from apps.domains.verification import verify_custom_domain_dns

print('=== Investigation of Custom Domain SSL Issue ===')

# Check current state
print('\n=== Current Database State ===')
services = Service.objects.all()
domains = Domain.objects.all()

print(f'Total services: {services.count()}')
print(f'Total domains: {domains.count()}')

if services.exists():
    for service in services:
        print(f'Service: {service.name} (ID: {service.id})')
        print(f'  Status: {service.status}')
        print(f'  Public Domain: {service.public_domain}')
        print(f'  Owner: {service.owner.username}')

if domains.exists():
    print('\n=== Domain Details ===')
    for domain in domains:
        print(f'Domain: {domain.domain_name} (ID: {domain.id})')
        print(f'  Service: {domain.service.name if domain.service else "None"}')
        print(f'  Status: {domain.status}')
        print(f'  SSL Active: {domain.ssl_active}')
        print(f'  Verified: {domain.verified}')
        print(f'  DNS Expected: {domain.dns_expected}')
        print(f'  DNS Actual: {domain.dns_actual}')
        print(f'  Last Error: {domain.last_error}')
        print(f'  Created: {domain.created_at}')
        print(f'  Updated: {domain.updated_at}')
        print('---')

# Test DNS verification with the actual platform domain
print('\n=== Testing DNS Verification ===')
config = PlatformConfig.load()

if domains.exists():
    for domain in domains:
        print(f'Testing domain: {domain.domain_name}')
        result = verify_custom_domain_dns(domain, config)
        print(f'  Verified: {result.verified}')
        print(f'  Expected: {result.expected}')
        print(f'  Actual: {result.actual}')
        print(f'  Matched by: {result.matched_by}')
        print(f'  Error: {result.error}')

        # Check if domain should be verified based on current DNS
        if result.verified and domain.status != DomainStatus.DNS_VERIFIED:
            print(f'  -> DNS is verified but status is still {domain.status}')
            print('  -> This indicates the background task may not be running')
        elif not result.verified and domain.status in [DomainStatus.DNS_VERIFIED, DomainStatus.ACTIVE]:
            print(f'  -> DNS is not verified but status is {domain.status}')
            print('  -> This indicates a potential issue with DNS verification logic')

print('\n=== Testing with Platform Domain ===')
# Test with the actual platform domain
platform_domain = 'grid.smsly.cloud'
print(f'Testing platform domain: {platform_domain}')

class MockDomain:
    def __init__(self, domain_name):
        self.domain_name = domain_name
        self.service = None

mock_domain = MockDomain(platform_domain)
result = verify_custom_domain_dns(mock_domain, config)
print(f'  Verified: {result.verified}')
print(f'  Expected: {result.expected}')
print(f'  Actual: {result.actual}')
print(f'  Matched by: {result.matched_by}')
print(f'  Error: {result.error}')

print('\n=== Analysis Complete ===')
