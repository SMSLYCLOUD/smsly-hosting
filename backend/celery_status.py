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

print("=== Celery System Status Check ===\n")

# Check Celery configuration and status
try:
    from config.celery import app
    print("1. Celery Configuration")
    print(f"  Broker URL: {app.conf.broker_url}")
    print(f"  Result Backend: {app.conf.result_backend}")
    print(f"  Task Default Queue: {app.conf.task_default_queue}")

    # Check registered tasks
    print(f"\n2. Registered Tasks ({len(app.tasks)})")
    domain_tasks = []
    for task_name, task_obj in app.tasks.items():
        if 'domain' in task_name.lower() or 'ssl' in task_name.lower():
            domain_tasks.append((task_name, task_obj))

    print(f"  Domain/SSL Tasks ({len(domain_tasks)}):")
    for task_name, task_obj in domain_tasks:
        print(f"    - {task_name}")

    # Check if our specific task is registered
    domain_task_name = 'apps.domains.tasks.verify_dns_and_provision_ssl_task'
    if domain_task_name in app.tasks:
        print(f"  ✅ Domain task is registered: {domain_task_name}")
    else:
        print(f"  ❌ Domain task NOT registered: {domain_task_name}")

    # Check beat schedule
    print("\n3. Beat Schedule")
    beat_schedule = app.conf.beat_schedule
    print(f"  Total scheduled tasks: {len(beat_schedule)}")

    ssl_domain_tasks = {k: v for k, v in beat_schedule.items()
                       if 'ssl' in k.lower() or 'domain' in k.lower() or 'certificate' in k.lower()}

    if ssl_domain_tasks:
        print("  SSL/Domain Related Scheduled Tasks:")
        for name, config in ssl_domain_tasks.items():
            print(f"    - {name}")
            print(f"      Task: {config.get('task', 'Unknown')}")
            print(f"      Schedule: {config.get('schedule', 'Unknown')}")
    else:
        print("  No SSL/Domain tasks scheduled")

    # Check task queues
    print("\n4. Task Queues")
    queues = app.conf.task_queues
    print(f"  Defined queues: {len(queues)}")
    for queue in queues:
        print(f"    - {queue.name}")

    # Check task routing
    print("\n5. Task Routing")
    routes = app.conf.task_routes
    domain_routes = {k: v for k, v in routes.items()
                    if 'domain' in k.lower() or 'ssl' in k.lower()}

    if domain_routes:
        print("  Domain/SSL Task Routes:")
        for task, route in domain_routes.items():
            print(f"    - {task}: {route}")
    else:
        print("  No specific domain/ssl task routes")

except Exception as e:
    print(f"Error checking Celery status: {e}")
    import traceback
    traceback.print_exc()

print("\n6. Manual Task Testing")
print("-" * 40)

# Test if we can manually trigger the task
try:
    from apps.domains.models import Domain
    from apps.domains.tasks import verify_dns_and_provision_ssl_task

    domains = Domain.objects.all()
    if domains.exists():
        domain = domains.first()
        print(f"Found domain: {domain.domain_name}")

        # Test task execution
        print("Testing task execution...")
        result = verify_dns_and_provision_ssl_task(domain.id)
        print(f"Task result: {result}")

        # Check if task updated the domain
        domain.refresh_from_db()
        print(f"Domain status after task: {domain.status}")
        print(f"Domain verified after task: {domain.verified}")

        if domain.status != 'dns_pending':
            print("✅ Task execution appears to work")
        else:
            print("❌ Task execution didn't change domain status")

    else:
        print("No domains found for testing")

except Exception as e:
    print(f"Error testing task: {e}")
    import traceback
    traceback.print_exc()

print("\n7. Beat Task Check")
print("-" * 40)

# Check if the SSL monitoring task is properly configured
try:
    from apps.cloud.services.ssl_monitor import check_ssl_certificates_task

    print(f"SSL Monitor Task: {check_ssl_certificates_task.name}")
    print(f"Task in beat schedule: {'check-ssl-certificates-every-6h' in app.conf.beat_schedule}")

    # Test the SSL monitoring task
    print("Testing SSL monitoring task...")
    check_ssl_certificates_task()
    print("SSL monitoring task executed successfully")

except Exception as e:
    print(f"Error testing SSL monitoring task: {e}")

print("\n=== End Celery Status Check ===")
