import os
import sys
import django
from django.urls import resolve, Resolver404

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

paths_to_test = [
    '/api/v1/services/check-domain/',
    '/api/v1/services/check-domain',
    '/api/v1/services/',
]

for p in paths_to_test:
    try:
        match = resolve(p)
        print(f"Path '{p}': Matched {match.view_name}")
    except Resolver404:
        print(f"Path '{p}': Not Found")
