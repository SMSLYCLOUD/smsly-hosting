#!/usr/bin/env python
import os
import socket
import sys

import django
import dns.resolver

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Setup Django
django.setup()

print("=== Detailed DNS Verification Analysis ===\n")

# Check DNS resolution manually
print("1. Manual DNS Resolution Tests")
print("-" * 40)

test_domains = [
    'grid.smsly.cloud',
    'working-test.example.com',
    'test-service.grid.smsly.cloud'
]

for domain in test_domains:
    print(f"Testing domain: {domain}")
    try:
        # Test A record resolution
        answers = dns.resolver.resolve(domain, 'A')
        a_records = [str(r) for r in answers]
        print(f"  A Records: {a_records}")

        # Test CNAME resolution
        try:
            cname_answers = dns.resolver.resolve(domain, 'CNAME')
            cname_records = [str(r) for r in cname_answers]
            print(f"  CNAME Records: {cname_records}")
        except Exception as e:
            print(f"  CNAME Records: None (error: {e})")

    except Exception as e:
        print(f"  DNS Resolution Error: {e}")

    # Test socket connection
    try:
        ip = socket.gethostbyname(domain)
        print(f"  Socket IP: {ip}")
    except socket.gaierror:
        print("  Socket Resolution: Failed")

    print()

# Check Django DNS verification function
print("2. Django DNS Verification Function")
print("-" * 40)

from apps.deployments.models import PlatformConfig
from apps.domains.verification import _expected_targets, verify_custom_domain_dns

try:
    config = PlatformConfig.load()
    print(f"Config Domain: {config.domain}")
    print(f"Config Server IP: {getattr(config, 'server_ip', 'Not set')}")

    # Test _expected_targets function
    class MockDomain:
        def __init__(self, domain_name):
            self.domain_name = domain_name
            self.service = None

    mock_domain = MockDomain('working-test.example.com')
    expected_cnames, expected_ips = _expected_targets(mock_domain, config)
    print(f"Expected CNAMEs: {expected_cnames}")
    print(f"Expected IPs: {expected_ips}")

    # Test verification
    result = verify_custom_domain_dns(mock_domain, config)
    print("Verification Result:")
    print(f"  Verified: {result.verified}")
    print(f"  Expected: {result.expected}")
    print(f"  Actual: {result.actual}")
    print(f"  Error: {result.error}")

except Exception as e:
    print(f"Error in Django DNS verification: {e}")
    import traceback
    traceback.print_exc()

print()

# Check Domain model and task
print("3. Domain Model and Task Analysis")
print("-" * 40)

from apps.domains.models import Domain

try:
    domains = Domain.objects.all()
    print(f"Total domains: {domains.count()}")

    if domains.exists():
        domain = domains.first()
        print(f"Domain: {domain.domain_name}")
        print(f"Status: {domain.status}")
        print(f"Verified: {domain.verified}")
        print(f"SSL Active: {domain.ssl_active}")
        print(f"DNS Expected: {domain.dns_expected}")
        print(f"DNS Actual: {domain.dns_actual}")
        print(f"Last Error: {domain.last_error}")

        # Test task execution step by step
        print("\nTesting task execution step by step:")

        # Step 1: Get domain
        domain_obj = Domain.objects.get(id=domain.id)
        print(f"  1. Retrieved domain: {domain_obj.domain_name}")

        # Step 2: Check initial status
        old_status = domain_obj.status
        print(f"  2. Initial status: {old_status}")

        # Step 3: Execute task logic manually
        print("  3. Executing task logic...")

        # Simulate the task logic
        if old_status not in ['dns_verified', 'ssl_provisioning', 'active']:
            domain_obj.status = 'dns_pending'
            domain_obj.save(update_fields=['status'])
            print(f"  3a. Status set to: {domain_obj.status}")

        # Step 4: Run DNS verification
        result = verify_custom_domain_dns(domain_obj, config)
        print(f"  3b. DNS verification result: {result.verified}")

        # Step 5: Update domain based on result
        if result.verified:
            domain_obj.status = 'dns_verified'
            domain_obj.verified = True
            domain_obj.last_error = None
            domain_obj.save(update_fields=['status', 'verified', 'last_error'])
            print(f"  3c. Domain updated to: {domain_obj.status}")
        else:
            domain_obj.status = 'dns_pending'
            domain_obj.last_error = result.error
            domain_obj.verified = False
            domain_obj.save(update_fields=['status', 'last_error', 'verified'])
            print(f"  3c. Domain updated to: {domain_obj.status}")
            print(f"  3d. Error: {result.error}")

        # Step 6: Check final status
        domain_obj.refresh_from_db()
        print(f"  4. Final status: {domain_obj.status}")
        print(f"  5. Final verified: {domain_obj.verified}")

        if domain_obj.status != old_status:
            print("  -> Task logic works correctly")
        else:
            print("  -> Task logic may have an issue")

except Exception as e:
    print(f"Error in domain task analysis: {e}")
    import traceback
    traceback.print_exc()

print()

# Check if task should trigger Caddy reload
print("4. Caddy Reload Analysis")
print("-" * 40)

try:
    from apps.deployments.services.caddy_manager import generate_caddyfile

    # Test Caddyfile generation
    print("Testing Caddyfile generation...")
    caddyfile_content = generate_caddyfile(config)
    print(f"Caddyfile generated: {len(caddyfile_content)} characters")

    # Check if domain is included in Caddyfile
    if 'working-test.example.com' in caddyfile_content:
        print("  -> Domain is included in Caddyfile")
    else:
        print("  -> Domain is NOT included in Caddyfile")

    # Check Caddyfile structure
    lines = caddyfile_content.split('\n')
    print(f"  Caddyfile has {len(lines)} lines")

    # Look for domain blocks
    domain_blocks: list = []
    current_block: list = []
    for line in lines:
        if line.strip() and not line.startswith('#') and not line.startswith('{') and line.strip().endswith('{'):
            # Start of a new block
            if current_block:
                domain_blocks.append('\n'.join(current_block))
            current_block = [line]
        elif current_block and line.strip() == '}':
            current_block.append(line)
            domain_blocks.append('\n'.join(current_block))
            current_block = []
        elif current_block:
            current_block.append(line)

    print(f"  Found {len(domain_blocks)} domain blocks")
    for i, block in enumerate(domain_blocks[:3]):  # Show first 3 blocks
        domain_name = block.split('{')[0].strip()
        print(f"    Block {i+1}: {domain_name}")

except Exception as e:
    print(f"Error in Caddy analysis: {e}")

print("\n=== End Analysis ===")
