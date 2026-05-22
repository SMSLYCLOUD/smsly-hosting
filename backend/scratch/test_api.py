import requests
import sys

URL_BASE = "http://127.0.0.1:8000/api/v1/services"
SERVICE_ID = "bd0e66ee-4e16-4d2a-a461-9e2797caedeb"  # from logs

# Test 1: PATCH Service
res = requests.patch(f"{URL_BASE}/{SERVICE_ID}/", json={"name": "test"})
print(f"PATCH /services/: {res.status_code} {res.text}")

# Test 2: GET file-browse
res = requests.get(f"{URL_BASE}/{SERVICE_ID}/file-browse/?path=/app")
print(f"GET file-browse: {res.status_code} {res.text}")

# Test 3: POST previews
res = requests.post(f"{URL_BASE}/{SERVICE_ID}/previews/", json={"branch_name": "main", "commit_sha": "12345"})
print(f"POST previews: {res.status_code} {res.text}")
