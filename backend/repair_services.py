import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Addon

def repair():
    # Fix AI Router
    ai_router = Service.objects.filter(name__icontains='ai-router').first()
    if ai_router:
        postgres = Addon.objects.filter(service=ai_router, addon_type='POSTGRES').first()
        if postgres and postgres.connection_url:
            ev, created = EnvironmentVariable.objects.update_or_create(
                service=ai_router,
                key='DATABASE_URL',
                defaults={'value': postgres.connection_url, 'is_secret': True}
            )
            print(f"Updated DATABASE_URL for {ai_router.name}: {ev.value}")
        else:
            print(f"No active Postgres addon found for {ai_router.name}")
    else:
        print("AI Router service not found")

    # Fix other services with missing DATABASE_URL but having POSTGRES addon
    services = Service.objects.all()
    for s in services:
        addons = Addon.objects.filter(service=s, status='ACTIVE')
        for a in addons:
            key_map = {
                'POSTGRES': 'DATABASE_URL',
                'REDIS': 'REDIS_URL',
                'MONGODB': 'MONGODB_URI', # Match the provisioner key
                'MYSQL': 'MYSQL_URL',
            }
            key = key_map.get(a.addon_type)
            if key:
                # Check if it's missing or has placeholder
                ev = EnvironmentVariable.objects.filter(service=s, key=key).first()
                if not ev or '${' in str(ev.value) or 'postgresql://...' in str(ev.value):
                   ev, created = EnvironmentVariable.objects.update_or_create(
                       service=s,
                       key=key,
                       defaults={'value': a.connection_url, 'is_secret': True}
                   )
                   print(f"Repaired {key} for {s.name}: {ev.value}")

if __name__ == '__main__':
    repair()
