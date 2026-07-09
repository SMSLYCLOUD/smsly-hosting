#!/usr/bin/env python3
import os
import sys

# Add backend to path to import django/celery
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

import time

from apps.deployments.tasks_metrics import collect_metrics_task


def main():
    print("Starting Celery DB Pool Stress Test...")
    print("Dispatching 100 async tasks...")

    tasks = []
    for _ in range(100):
        # We call the task asynchronously so Celery workers pick it up
        t = collect_metrics_task.delay()
        tasks.append(t)

    print("Tasks dispatched. Waiting for completion...")

    success = 0
    failure = 0

    # Wait for a bit to let them process
    time.sleep(10)

    for t in tasks:
        if t.ready():
            if t.successful():
                success += 1
            else:
                failure += 1

    print(f"Finished checks. Success: {success}, Failure: {failure}, Pending: {len(tasks) - success - failure}")

if __name__ == "__main__":
    main()
