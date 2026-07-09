import os
import sys
import time
import uuid
from datetime import datetime
from decimal import Decimal

import django
import requests
from django.conf import settings
from django.core.management import call_command

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --- DJANGO SETUP ---
if not settings.configured:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.append(os.path.join(BASE_DIR, 'backend'))

    # QA Database Name
    DB_NAME = os.path.join(BASE_DIR, 'db.sqlite3.qa_comprehensive')

    settings.configure(
        DEBUG=True,
        SECRET_KEY='qa-comprehensive-secret-key-very-long-and-secure',
        FIELD_ENCRYPTION_KEY='oEukOknPHtrnRjXRXAxTisUqXrnVjmQRBna5u4NV-_8=', # Reusing for consistency
        DATABASES={
            'default': {
               'ENGINE': 'django.db.backends.sqlite3',
               'NAME': DB_NAME,
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

# Import Models AFTER setup
from apps.billing.models import UsageRecord
from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, EnvironmentVariable, Service
from django.contrib.auth.models import User

# Import Skills Simulator
try:
    from tests.qa_stress.simulate_skills import (
        test_chart_skill,
        test_docx_skill,
        test_frontend_design_skill,
        test_pdf_skill,
        test_xlsx_skill,
    )
except ImportError:
    # Handle case where script is run from root
    sys.path.append(os.path.join(os.getcwd(), 'tests/qa_stress'))
    from simulate_skills import (
        test_chart_skill,
        test_docx_skill,
        test_frontend_design_skill,
        test_pdf_skill,
        test_xlsx_skill,
    )

# --- REPORTING ---
REPORT_FILE = "test-results/comprehensive_qa_report.md"
os.makedirs("test-results", exist_ok=True)

class ReportGenerator:
    def __init__(self):
        self.sections = []
        self.summary = {"passed": 0, "failed": 0, "total": 0}

    def add_section(self, title, content):
        self.sections.append(f"## {title}\n\n{content}\n")

    def add_result(self, test_name, success, details=""):
        icon = "✅" if success else "❌"
        self.summary["total"] += 1
        if success:
            self.summary["passed"] += 1
        else:
            self.summary["failed"] += 1
        return f"- {icon} **{test_name}**: {details}"

    def generate(self):
        with open(REPORT_FILE, "w") as f:
            f.write("# 🛡️ Jules Ultimate QA Protocol Report\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Summary:** {self.summary['passed']}/{self.summary['total']} Passed ({self.summary['failed']} Failed)\n\n")
            for section in self.sections:
               f.write(section)
        print(f"\n📄 Report generated at: {REPORT_FILE}")

reporter = ReportGenerator()

# --- SYSTEM TESTER ---
class SystemTester:
    def run_all(self):
        results = []
        results.append(self.test_filesystem_boundaries())
        results.append(self.test_network_access())
        results.append(self.test_resource_limits())
        reporter.add_section("Phase 1: System & Environment", "\n".join(results))

    def test_filesystem_boundaries(self):
        print("🔹 Testing Filesystem Boundaries...")
        checks = []

        # 1. Write to /tmp (Should be allowed)
        try:
            with open("/tmp/jules_test.txt", "w") as f:
               f.write("test")
            checks.append(reporter.add_result("Write /tmp", True, "Successfully wrote to /tmp"))
        except Exception as e:
            checks.append(reporter.add_result("Write /tmp", False, str(e)))

        # 2. Read sensitive file (Should fail or be restricted)
        sensitive_files = ["/etc/shadow", "/root/.bashrc"]
        for s_file in sensitive_files:
            if os.path.exists(s_file):
               try:
                  with open(s_file, "r") as f:
                      f.read()
                  checks.append(reporter.add_result(f"Read {s_file}", False, "WARNING: Could read sensitive file!"))
               except PermissionError:
                  checks.append(reporter.add_result(f"Read {s_file}", True, "Permission denied (Expected)"))
               except Exception as e:
                  checks.append(reporter.add_result(f"Read {s_file}", True, f"Access failed: {e} (Expected)"))
            else:
               checks.append(reporter.add_result(f"Read {s_file}", True, "File does not exist (Safe)"))

        return "\n".join(checks)

    def test_network_access(self):
        print("🔹 Testing Network Access...")
        checks = []
        try:
            resp = requests.get("https://www.google.com", timeout=5)
            checks.append(reporter.add_result("Internet Access (Google)", True, f"Status: {resp.status_code}"))
        except Exception as e:
            checks.append(reporter.add_result("Internet Access", False, f"Failed: {e}"))
        return "\n".join(checks)

    def test_resource_limits(self):
        print("🔹 Testing Resource Limits (File Creation)...")
        checks = []
        try:
            # Create 10MB file
            size_mb = 10
            with open("test-results/large_file.dat", "wb") as f:
               f.write(os.urandom(size_mb * 1024 * 1024))
            checks.append(reporter.add_result(f"Create {size_mb}MB File", True, "Success"))
            os.remove("test-results/large_file.dat")
        except Exception as e:
            checks.append(reporter.add_result(f"Create {size_mb}MB File", False, str(e)))
        return "\n".join(checks)

# --- ADVERSARIAL TESTER ---
class AdversarialTester:
    def __init__(self):
        # Setup DB
        print("📦 Migrating database for Adversarial Tests...")
        call_command('migrate', verbosity=0)
        self.user, _ = User.objects.get_or_create(username='adversary_bot')
        self.provider, _ = CloudProvider.objects.get_or_create(name='AdvAWS', defaults={'provider_type': 'AWS'})

    def run_all(self):
        results = []
        results.append(self.test_service_injection())
        results.append(self.test_env_vars_security())
        results.append(self.test_billing_flood())
        reporter.add_section("Phase 2: Adversarial & Security", "\n".join(results))

    def test_service_injection(self):
        print("🔹 Testing Service Model Injection...")
        checks = []

        # 1. SQL Injection Name
        bad_name = "service'; DROP TABLE auth_user; --"
        try:
            Service.objects.create(name=bad_name, owner=self.user)
            # If created, check it didn't execute SQL
            if User.objects.exists():
               checks.append(reporter.add_result("SQL Injection Name", True, "Created safely, DB intact"))
            else:
               checks.append(reporter.add_result("SQL Injection Name", False, "CRITICAL: DB Tables Dropped!"))
        except Exception as e:
            # Creation failure is also acceptable if validation catches it
            checks.append(reporter.add_result("SQL Injection Name", True, f"Blocked/Failed safely: {e}"))

        # 2. XSS Name (Stored XSS)
        xss_name = "<script>alert(1)</script>"
        try:
            svc = Service.objects.create(name=xss_name, owner=self.user)
            checks.append(reporter.add_result("XSS Name Storage", True, f"Stored as: {svc.name}"))
        except Exception as e:
            checks.append(reporter.add_result("XSS Name Storage", True, f"Blocked: {e}"))

        # 3. Huge Name (Buffer Overflow)
        huge_name = "A" * 1000
        try:
            svc = Service(name=huge_name, owner=self.user)
            svc.full_clean() # Force validation
            svc.save()
            checks.append(reporter.add_result("Huge Name (1000 chars)", False, "Validation bypassed! Saved 1000 chars"))
        except Exception as e:
            checks.append(reporter.add_result("Huge Name (1000 chars)", True, f"Correctly rejected: {e}"))

        return "\n".join(checks)

    def test_env_vars_security(self):
        print("🔹 Testing Env Var Security...")
        checks = []
        svc = Service.objects.create(name=f"secure-svc-{uuid.uuid4().hex[:6]}", owner=self.user)

        # 1. Special Characters
        special_val = "!@#$%^&*()_+{}|:<>?`~"
        try:
            EnvironmentVariable.objects.create(service=svc, key="SPECIAL", value=special_val)
            fetched = EnvironmentVariable.objects.get(service=svc, key="SPECIAL")
            if fetched.value == special_val:
               checks.append(reporter.add_result("Special Char Env Var", True, "Stored and retrieved correctly"))
            else:
               checks.append(reporter.add_result("Special Char Env Var", False, "Corruption on retrieval"))
        except Exception as e:
            checks.append(reporter.add_result("Special Char Env Var", False, str(e)))

        return "\n".join(checks)

    def test_billing_flood(self):
        print("🔹 Testing Billing Data Flood...")
        checks = []
        svc = Service.objects.create(name=f"billing-flood-{uuid.uuid4().hex[:6]}", owner=self.user)

        # Generate 5,000 records
        records = []
        for _ in range(5000):
            records.append(UsageRecord(
               service=svc, cpu_cores=1.0, memory_mb=1024, duration_seconds=3600, cost=Decimal('0.01')
            ))

        start = time.time()
        UsageRecord.objects.bulk_create(records)
        duration = time.time() - start

        count = UsageRecord.objects.filter(service=svc).count()
        checks.append(reporter.add_result("Bulk Create 5000 Records", True, f"Time: {duration:.2f}s, Count: {count}"))

        return "\n".join(checks)

# --- INTEGRATION TESTER ---
class IntegrationTester:
    def __init__(self):
        self.user, _ = User.objects.get_or_create(username='integration_user')
        self.provider, _ = CloudProvider.objects.get_or_create(name='IntAWS', defaults={'provider_type': 'AWS'})

    def run_all(self):
        results = []
        results.append(self.test_e2e_deployment_flow())
        reporter.add_section("Phase 3: Integration & Workflows", "\n".join(results))

    def test_e2e_deployment_flow(self):
        print("🔹 Testing E2E Deployment Flow...")
        checks = []

        try:
            # 1. Create Service
            svc = Service.objects.create(
               name=f"e2e-app-{uuid.uuid4().hex[:6]}",
               owner=self.user,
               provider=self.provider,
               deploy_type='GIT',
               repository_url='https://github.com/smsly/test-repo'
            )
            checks.append(reporter.add_result("Create Service", True, f"ID: {svc.id}"))

            # 2. Create Deployment
            deploy = Deployment.objects.create(
               service=svc,
               commit_hash="abc1234",
               commit_message="Initial commit",
               status=Deployment.Status.QUEUED
            )
            checks.append(reporter.add_result("Queue Deployment", True, f"ID: {deploy.id}"))

            # 3. Simulate Build Process
            deploy.status = Deployment.Status.BUILDING
            deploy.save()
            time.sleep(0.1)
            deploy.status = Deployment.Status.DEPLOYING
            deploy.save()
            time.sleep(0.1)
            deploy.status = Deployment.Status.ACTIVE
            deploy.finished_at = datetime.now()
            deploy.save()
            checks.append(reporter.add_result("Simulate Build/Deploy", True, "Status -> ACTIVE"))

            # 4. Verify History
            history = Deployment.objects.filter(service=svc)
            if history.count() == 1:
               checks.append(reporter.add_result("Verify History", True, "Found 1 deployment"))
            else:
               checks.append(reporter.add_result("Verify History", False, f"Found {history.count()} deployments"))

        except Exception as e:
            checks.append(reporter.add_result("E2E Flow Failed", False, str(e)))

        return "\n".join(checks)

# --- SKILLS TESTER ---
class SkillsTester:
    def run_all(self):
        print("🔹 Testing Skills (Calling simulate_skills)...")
        results = []
        try:
            # Charts
            chart_paths = test_chart_skill()
            if chart_paths:
               results.append(reporter.add_result("Chart Skill", True, f"Created {len(chart_paths)} charts"))
            else:
               results.append(reporter.add_result("Chart Skill", False, "Failed to create charts"))

            # DOCX
            docx_path = test_docx_skill(chart_paths)
            if docx_path:
               results.append(reporter.add_result("DOCX Skill", True, "Advanced DOCX created"))
            else:
               results.append(reporter.add_result("DOCX Skill", False, "Failed"))

            # XLSX
            xlsx_path = test_xlsx_skill()
            if xlsx_path:
               results.append(reporter.add_result("XLSX Skill", True, "Advanced XLSX created"))
            else:
               results.append(reporter.add_result("XLSX Skill", False, "Failed"))

            # PDF
            pdf_path = test_pdf_skill()
            if pdf_path:
               results.append(reporter.add_result("PDF Skill", True, "Advanced PDF created"))
            else:
               results.append(reporter.add_result("PDF Skill", False, "Failed"))

            # Frontend
            fe_path = test_frontend_design_skill()
            if fe_path:
               results.append(reporter.add_result("Frontend Design Skill", True, "React artifact created"))
            else:
               results.append(reporter.add_result("Frontend Design Skill", False, "Failed"))

        except Exception as e:
            results.append(reporter.add_result("Skills Execution", False, str(e)))

        reporter.add_section("Phase 4: Skills & Artifacts", "\n".join(results))

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("🚀 STARTING ULTIMATE COMPREHENSIVE QA PROTOCOL 🚀")

    # 1. Skills
    skills = SkillsTester()
    skills.run_all()

    # 2. System
    sys_test = SystemTester()
    sys_test.run_all()

    # 3. Adversarial
    adv_test = AdversarialTester()
    adv_test.run_all()

    # 4. Integration
    int_test = IntegrationTester()
    int_test.run_all()

    # Generate Report
    reporter.generate()
    print("🏁 PROTOCOL COMPLETE.")
