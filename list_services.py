from apps.deployments.models import Service
print("SERVICES_START")
for s in Service.objects.all():
    print(f"{s.id} | {s.name} | {s.status} | {s.public_domain}")
print("SERVICES_END")
