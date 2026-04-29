#!/bin/bash
export PYTHONPATH=/app/backend
source /home/jules/.pyenv/versions/3.12.13/bin/activate || true
pip install -r requirements.txt > /dev/null 2>&1
python manage.py test apps.deployments.tests.test_mesh
