import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from apps.cloud.services.builder import _BUILDX_DEFAULT_BROKEN_MARKERS as m
print('MARKERS:', list(m))
test = 'ERROR: no such builder: default'
print('TEST:', repr(test))
print('LOWER:', repr(test.lower()))
for marker in m:
    print(' ', repr(marker), 'in lower?', marker in test.lower())
