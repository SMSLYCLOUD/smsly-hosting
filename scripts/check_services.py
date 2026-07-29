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

from apps.deployments.models import Service

print('=== Current Service Records ===')
services = Service.objects.all().order_by('id')
for service in services:
    print(f'ID: {service.id}')
    print(f'Name: {service.name}')
    print(f'Public Domain: {service.public_domain}')
    print(f'Public Domain Hidden: {getattr(service, "public_domain_hidden", False)}')
    print(f'Owner: {service.owner.username if service.owner else "None"}')
    print(f'Created: {service.created_at}')
    print(f'Updated: {service.updated_at}')
    print('---')

print(f'\nTotal services: {Service.objects.count()}')

# Check if there are custom domains in the JSONField
print('\n=== Checking custom_domains JSONField ===')
for service in services:
    custom_domains = getattr(service, 'custom_domains', None)
    if custom_domains:
        print(f'Service {service.id} has custom domains: {custom_domains}')
    else:
        print(f'Service {service.id} has no custom domains')
