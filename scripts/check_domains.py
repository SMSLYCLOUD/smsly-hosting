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

from apps.domains.models import Domain, DomainStatus

print('=== Current Domain Records ===')
domains = Domain.objects.all().order_by('id')
for domain in domains:
    service = domain.service
    print(f'ID: {domain.id}')
    print(f'Domain: {domain.domain_name}')
    print(f'Status: {domain.status}')
    print(f'SSL Active: {domain.ssl_active}')
    print(f'Service ID: {service.id if service else "None"}')
    print(f'Service: {service.name if service else "None"}')
    print(f'DNS Expected: {domain.dns_expected}')
    print(f'DNS Actual: {domain.dns_actual}')
    print(f'Last Error: {domain.last_error}')
    print(f'Created: {domain.created_at}')
    print(f'Updated: {domain.updated_at}')
    print('---')

print('\n=== Total Domain Count ===')
print(f'Total domains: {Domain.objects.count()}')
print(f'Pending domains: {Domain.objects.filter(status=DomainStatus.PENDING).count()}')
print(f'DNS pending domains: {Domain.objects.filter(status=DomainStatus.DNS_PENDING).count()}')
print(f'DNS verified domains: {Domain.objects.filter(status=DomainStatus.DNS_VERIFIED).count()}')
print(f'SSL provisioning domains: {Domain.objects.filter(status=DomainStatus.SSL_PROVISIONING).count()}')
print(f'Active domains: {Domain.objects.filter(status=DomainStatus.ACTIVE).count()}')
print(f'SSL failed domains: {Domain.objects.filter(status=DomainStatus.SSL_FAILED).count()}')
