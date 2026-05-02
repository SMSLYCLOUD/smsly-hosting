#!/usr/bin/env bash
set -e

echo "============================================="
echo "Running Custom Domain SSL Automation Tests"
echo "============================================="

cd backend

echo "Running DnsVerificationTests..."
python manage.py test apps.domains.tests -v 2

echo "Running Custom Domain Instant Routing Tests..."
python manage.py test apps.deployments.tests.test_custom_domain_instant_routing -v 2

echo "============================================="
echo "All tests passed successfully!"
echo "============================================="

echo "Running API Serializer and Retry Tests..."
python manage.py test apps.domains.tests_api -v 2
