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

from django_celery_results.models import TaskResult

print('=== Celery System Check ===')

# Check if Celery is properly configured
try:
    from config.celery import app
    print('Celery app loaded successfully')
    print(f'Broker URL: {app.conf.broker_url}')
    print(f'Result Backend: {app.conf.result_backend}')

    # Check registered tasks
    print(f'\nRegistered tasks: {len(app.tasks)}')
    domain_tasks = [task for task_name, task in app.tasks.items() if 'domain' in task_name.lower()]
    print(f'Domain-related tasks: {len(domain_tasks)}')
    for task in domain_tasks:
        print(f'  - {task.name}')

except Exception as e:
    print(f'Error loading Celery: {e}')

# Check task results
print('\n=== Task Results ===')
try:
    recent_tasks = TaskResult.objects.order_by('-date_done')[:10]
    print(f'Recent task results: {recent_tasks.count()}')

    domain_task_results = recent_tasks.filter(task_name__icontains='domain')
    print(f'Domain task results: {domain_task_results.count()}')

    for task in domain_task_results:
        print(f'  Task: {task.task_name}')
        print(f'  Status: {task.status}')
        print(f'  Date: {task.date_done}')
        if task.task_id:
            print(f'  ID: {task.task_id}')
        print('---')

except Exception as e:
    print(f'Error checking task results: {e}')

# Test the specific domain task
print('\n=== Testing Domain Task ===')
try:
    # Get existing domain
    from apps.domains.models import Domain
    from apps.domains.tasks import verify_dns_and_provision_ssl_task
    domain = Domain.objects.first()
    if domain:
        print(f'Testing task for domain: {domain.domain_name}')
        print(f'Task name: {verify_dns_and_provision_ssl_task.name}')
        print(f'Task delay available: {hasattr(verify_dns_and_provision_ssl_task, "delay")}')

        # Test calling the task directly (not async)
        print('Testing task execution (synchronous)...')
        result = verify_dns_and_provision_ssl_task(domain.id)
        print(f'Task result: {result}')

        # Check domain status after task execution
        domain.refresh_from_db()
        print(f'Domain status after task: {domain.status}')
        print(f'Domain verified after task: {domain.verified}')
        print(f'Domain last error: {domain.last_error}')
    else:
        print('No domains found for testing')

except Exception as e:
    print(f'Error testing domain task: {e}')

print('\n=== Manual Task Execution ===')
print('To manually run the domain verification task:')
print('python manage.py shell')
print('from apps.domains.models import Domain')
print('from apps.domains.tasks import verify_dns_and_provision_ssl_task')
print('domain = Domain.objects.first()')
print('if domain:')
print('    verify_dns_and_provision_ssl_task.delay(domain.id)')
