#!/usr/bin/env python
import os
import sys

import django
import pytest

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


@pytest.mark.django_db
def test_dns_verification():
    django.setup()

    from apps.deployments.models import PlatformConfig
    from apps.domains.models import Domain, DomainStatus
    from apps.domains.verification import verify_custom_domain_dns

    print('=== Testing DNS Verification Function ===')

    # Create a mock platform config
    config = PlatformConfig.load()

    # Test with a domain that should work
    test_domain = "test.example.com"
    print(f'Testing domain: {test_domain}')

    # Create a mock domain object
    class MockDomain:
        def __init__(self, domain_name):
            self.domain_name = domain_name
            self.service = None

    mock_domain = MockDomain(test_domain)

    # Test DNS verification
    try:
        result = verify_custom_domain_dns(mock_domain, config)
        print(f'Verified: {result.verified}')
        print(f'Expected: {result.expected}')
        print(f'Actual: {result.actual}')
        print(f'Matched by: {result.matched_by}')
        print(f'Error: {result.error}')
    except Exception as e:
        print(f'Error during DNS verification: {e}')

    print('\n=== Testing with Domain Model ===')

    # Try to create a test domain
    try:
        # First, check if there are any services
        from apps.deployments.models import Service
        services = Service.objects.all()
        if services.exists():
            service = services.first()
            print(f'Found service: {service.name} (ID: {service.id})')

            # Create a test domain
            domain = Domain.objects.create(
                domain_name="test.example.com",
                service=service,
                status=DomainStatus.PENDING
            )
            print(f'Created test domain: {domain.domain_name} (ID: {domain.id})')

            # Test DNS verification
            result = verify_custom_domain_dns(domain, config)
            print(f'Verified: {result.verified}')
            print(f'Expected: {result.expected}')
            print(f'Actual: {result.actual}')
            print(f'Matched by: {result.matched_by}')
            print(f'Error: {result.error}')

            # Clean up
            domain.delete()
            print('Cleaned up test domain')
        else:
            print('No services found in database')

    except Exception as e:
        print(f'Error during domain model test: {e}')
