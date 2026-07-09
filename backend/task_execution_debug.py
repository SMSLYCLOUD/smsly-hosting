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

print("=== Domain Task Execution Debug ===\n")

# 1. Check domain task execution in detail
print("1. Domain Task Execution Analysis")
print("-" * 40)

from apps.deployments.models import PlatformConfig
from apps.domains.models import Domain
from apps.domains.verification import verify_custom_domain_dns

try:
    config = PlatformConfig.load()
    domains = Domain.objects.all()

    if domains.exists():
        domain = domains.first()
        print(f"Domain: {domain.domain_name}")
        print(f"Current status: {domain.status}")
        print(f"Current verified: {domain.verified}")

        # Execute the task step by step as it would run
        print("\nExecuting task step by step...")

        # Step 1: Get domain
        domain_obj = Domain.objects.get(id=domain.id)
        print(f"1. Retrieved domain: {domain_obj.domain_name}")

        # Step 2: Check status and potentially update
        old_status = domain_obj.status
        print(f"2. Old status: {old_status}")

        if old_status not in ['dns_verified', 'ssl_provisioning', 'active']:
            domain_obj.status = 'dns_pending'
            domain_obj.save(update_fields=['status'])
            print(f"   Status updated to: {domain_obj.status}")

        # Step 3: Run DNS verification
        print("3. Running DNS verification...")
        result = verify_custom_domain_dns(domain_obj, config)
        print(f"   Verification result: {result.verified}")
        print(f"   Expected: {result.expected}")
        print(f"   Actual: {result.actual}")
        print(f"   Error: {result.error}")

        # Step 4: Update domain based on verification result
        if result.verified:
            print("4. DNS is verified - updating domain to dns_verified")
            domain_obj.status = 'dns_verified'
            domain_obj.verified = True
            domain_obj.last_error = None
            domain_obj.save(update_fields=['status', 'verified', 'last_error'])

            # Check if this should trigger Caddy reload
            if old_status not in ['dns_verified', 'ssl_provisioning', 'active']:
                print("   This should trigger Caddy reload")
                print("   However, Caddy reload is not happening in this test")
        else:
            print("4. DNS is not verified - updating domain to dns_pending")
            domain_obj.status = 'dns_pending'
            domain_obj.last_error = result.error
            domain_obj.verified = False
            domain_obj.save(update_fields=['status', 'last_error', 'verified'])
            print(f"   Error: {result.error}")

        # Step 5: Check final status
        domain_obj.refresh_from_db()
        print(f"5. Final status: {domain_obj.status}")
        print(f"   Final verified: {domain_obj.verified}")
        print(f"   Last error: {domain_obj.last_error}")

        # Check if the task actually ran
        if domain_obj.status != old_status:
            print("\n✓ Task execution logic works correctly")
        else:
            print("\n✗ Task execution may have an issue")

    else:
        print("No domains found for testing")

except Exception as e:
    print(f"Error in task execution analysis: {e}")
    import traceback
    traceback.print_exc()

print("\n2. Caddy Reload Analysis")
print("-" * 40)

try:
    from services.caddy_manager import apply_caddyfile, generate_caddyfile

    # Test Caddyfile generation
    print("Testing Caddyfile generation...")
    caddyfile_content = generate_caddyfile(config)
    print("Caddyfile generated successfully")

    # Check if our test domain should be included
    test_domain = 'working-test.example.com'
    if test_domain in caddyfile_content:
        print("Test domain should be included in generated Caddyfile")

        # Show the domain block
        lines = caddyfile_content.split('\n')
        for i, line in enumerate(lines):
            if test_domain in line:
                print(f"Domain block starts at line {i+1}:")
                # Print the block
                j = i
                while j < len(lines) and not (lines[j].strip() == '}' and j > i):
                    print(f"  {j+1:2d}: {lines[j]}")
                    j += 1
                    if lines[j].strip() == '}':
                        print(f"  {j+1:2d}: {lines[j]}")
                        break
                break
    else:
        print("Test domain should NOT be included in generated Caddyfile")
        print("This is expected because DNS verification failed")

    # Test Caddyfile application (dry run)
    print("\nTesting Caddyfile application...")
    result = apply_caddyfile(caddyfile_content, cloudflare_token="")
    print(f"Caddyfile apply result: {result}")

except Exception as e:
    print(f"Error in Caddy analysis: {e}")
    import traceback
    traceback.print_exc()

print("\n3. DNS Check Endpoint Analysis")
print("-" * 40)

# Check the domain check endpoint that Caddy calls
try:
    from apps.deployments.views import check_domain_view

    print("Testing domain check endpoint...")

    # Test with a domain that should pass
    print("Testing with grid.smsly.cloud (should pass)")
    request = type('Request', (), {'GET': type('QueryDict', (), {'get': lambda self, k: 'grid.smsly.cloud'})()})()
    response = check_domain_view(request, 'grid.smsly.cloud')
    print(f"Response status: {response.status_code}")
    print(f"Response content: {response.content}")

    # Test with our test domain (should fail)
    print("\nTesting with working-test.example.com (should fail)")
    request = type('Request', (), {'GET': type('QueryDict', (), {'get': lambda self, k: 'working-test.example.com'})()})()
    response = check_domain_view(request, 'working-test.example.com')
    print(f"Response status: {response.status_code}")
    print(f"Response content: {response.content}")

except Exception as e:
    print(f"Error in domain check endpoint analysis: {e}")
    import traceback
    traceback.print_exc()

print("\n4. Task Registration Analysis")
print("-" * 40)

try:
    from config.celery import app

    print(f"Celery app: {app}")
    print(f"Broker URL: {app.conf.broker_url}")
    print(f"Result Backend: {app.conf.result_backend}")

    # Check if our task is registered
    task_name = 'apps.domains.tasks.verify_dns_and_provision_ssl_task'
    if task_name in app.tasks:
        print("✓ Domain task is registered")
        task = app.tasks[task_name]
        print(f"Task info: {task}")
    else:
        print("✗ Domain task is NOT registered")

    # Check beat schedule
    beat_schedule = app.conf.beat_schedule
    ssl_tasks = {k: v for k, v in beat_schedule.items() if 'ssl' in k.lower()}
    if ssl_tasks:
        print("SSL tasks in beat schedule:")
        for name, config in ssl_tasks.items():
            print(f"  {name}: {config}")
    else:
        print("No SSL tasks in beat schedule")

except Exception as e:
    print(f"Error in task registration analysis: {e}")
    import traceback
    traceback.print_exc()

print("\n=== End Task Execution Debug ===")
