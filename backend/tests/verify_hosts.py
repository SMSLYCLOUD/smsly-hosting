from decouple import config, Csv
import os

# Simulate .env loading
os.environ['ALLOWED_HOSTS'] = 'hosting.smsly.cloud,www.hosting.smsly.cloud,localhost,127.0.0.1,backend'

def check_host(host):
    allowed = config('ALLOWED_HOSTS', default='*', cast=Csv())
    print(f"Checking host '{host}' against {allowed}...")
    if host in allowed:
        print("✅ Allowed")
    else:
        print("❌ Disallowed")

if __name__ == "__main__":
    check_host("backend")
    check_host("hosting.smsly.cloud")
    check_host("malicious.site")
