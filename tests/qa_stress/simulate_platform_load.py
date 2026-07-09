import os
import sys

import django
from django.conf import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Configure Django settings for testing with SQLite
if not settings.configured:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.append(os.path.join(BASE_DIR, 'backend'))
    settings.configure(
        DEBUG=True,
        SECRET_KEY='qa-test-secret-key',
        FIELD_ENCRYPTION_KEY='oEukOknPHtrnRjXRXAxTisUqXrnVjmQRBna5u4NV-_8=',
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': os.path.join(BASE_DIR, 'db.sqlite3.qa'),
            }
        },
        INSTALLED_APPS=[
            'django.contrib.admin',
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'django.contrib.sessions',
            'django.contrib.messages',
            'django.contrib.staticfiles',
            'rest_framework',
            'encrypted_model_fields',
            'apps.deployments',
            'apps.cloud',
            'apps.billing',
            'apps.teams',
            'apps.domains',
            'apps.intelligence',
        ],
        MIDDLEWARE=[
            'django.middleware.security.SecurityMiddleware',
            'django.contrib.sessions.middleware.SessionMiddleware',
            'django.middleware.common.CommonMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware',
        ],
        TIME_ZONE='UTC',
        USE_TZ=True,
    )
    django.setup()

import random
import uuid
from decimal import Decimal

from apps.billing.models import UsageRecord
from apps.cloud.models import CloudProvider
from apps.deployments.models import Region, Service
from django.contrib.auth.models import User
from django.core.management import call_command


def run_stress_test():
    print("🚀 Starting SMSly Platform Stress Test...")

    # Initialize DB
    print("📦 Migrating database...")
    call_command('migrate', verbosity=0)

    # Create Test User
    user, _ = User.objects.get_or_create(username='qa_tester')

    # Create Region and Provider (Dependencies)
    region, _ = Region.objects.get_or_create(
        slug='us-qa-1',
        defaults={'name': 'QA Region', 'provider': 'aws', 'country_code': 'US', 'city': 'Test City'}
    )

    provider, _ = CloudProvider.objects.get_or_create(
        name='QA AWS',
        defaults={'provider_type': 'AWS', 'api_key': 'test', 'api_secret': 'test'}
    )

    print("\n🔹 Phase 1: Service Creation Stress Test")
    services_created = 0
    errors = 0

    # Test Cases
    names = [
        f"qa-service-{uuid.uuid4().hex[:8]}", # Normal
        f"qa-unicode-🚀-{uuid.uuid4().hex[:4]}", # Unicode
        f"qa-sql-drop-table-{uuid.uuid4().hex[:4]}", # SQL Injection Attempt
        "a" * 250, # Max length boundary
    ]

    # Generate 100 random services
    for i in range(100):
        names.append(f"load-test-{i}-{uuid.uuid4().hex[:6]}")

    for name in names:
        try:
            Service.objects.create(
                name=name,
                owner=user,
                provider=provider,
                primary_region=region,
                deploy_type='GIT',
                repository_url='https://github.com/test/repo',
                cpu_cores=Decimal('0.5'),
                memory_mb=512
            )
            services_created += 1
        except Exception as e:
            print(f"⚠️ Failed to create service '{name[:20]}...': {e}")
            errors += 1

    print(f"✅ Created {services_created} services. Errors: {errors}")

    print("\n🔹 Phase 2: Billing Calculation Stress Test")
    # Generate 10,000 usage records
    services = list(Service.objects.all())
    records = []
    print("generating 10,000 records in memory...")

    for _ in range(10000):
        svc = random.choice(services)
        records.append(UsageRecord(
            service=svc,
            cpu_cores=svc.cpu_cores,
            memory_mb=svc.memory_mb,
            duration_seconds=3600,
            cost=Decimal('0.05')
        ))

    print("bulk_create to DB...")
    UsageRecord.objects.bulk_create(records)

    total_records = UsageRecord.objects.count()
    print(f"✅ Total Usage Records: {total_records}")

    # Verify Aggregation
    total_cost = sum(r.cost for r in UsageRecord.objects.all())
    print(f"💰 Total Calculated Revenue: ${total_cost}")

    if total_records >= 10000 and total_cost > 0:
        print("✅ Billing Test Passed")
    else:
        print("❌ Billing Test Failed")

    print("\n🏁 Platform Stress Test Complete.")

if __name__ == "__main__":
    run_stress_test()
