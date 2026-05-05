from apps.deployments.models import ManagedServer
s = ManagedServer.objects.filter(host='153.75.247.117').first()
if s:
    s.api_url = 'http://153.75.247.117'
    s.save()
    print(f"Updated API URL for {s.host} to {s.api_url}")
else:
    print("Server not found")
