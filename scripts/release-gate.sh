#!/bin/bash
set -euo pipefail

echo "==================================="
echo " RUNNING GRID RELEASE GATES"
echo "==================================="

# 1. Backend tests
echo "==> Running standard backend unit & integration tests..."
cd backend
source venv/bin/activate
DJANGO_SETTINGS_MODULE=config.settings python manage.py test apps.deployments.tests.test_ecosystem_graph apps.deployments.tests.test_ecosystem_deploy_api apps.deployments.tests.test_ecosystem_deploy_service apps.deployments.tests.test_ecosystem_stress apps.core.tests_security apps.core.tests_fuzz apps.core.tests_observability tests.invariants.test_deployment_invariants tests.invariants.test_transfer_invariants apps.deployments.tests.test_remote_lifecycle_new
cd ..

echo "==================================="
echo " ✅ RELEASE GATES PASSED SUCCESSFULLY"
echo "==================================="
