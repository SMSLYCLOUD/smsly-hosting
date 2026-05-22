import os
import sys
import django
from django.urls import resolve

# Set path to the backend dir so config can be imported
sys.path.append("c:\\Users\\osaretin\\Documents\\SMSLY\\SMSLY_CORE\\smsly-hosting\\backend")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

match = resolve("/api/v1/services/62b37eaf-e305-48cd-91cc-47b3233199e1/previews/")
print("Match:", match.url_name, match.func.__name__)
try:
    print("Class:", match.func.view_class.__name__)
except:
    pass
print("Args:", match.args, "Kwargs:", match.kwargs)
