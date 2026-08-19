import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()
from apps.deployments.models.core import ManagedServer
from apps.deployments.models import PlatformConfig

config = PlatformConfig.load()
print(f"Base domain: {config.domain}")

node = ManagedServer.objects.filter(is_primary=False, is_lite_agent=False).first()
if not node:
    print("No full node found")
else:
    node_slug = str(node.id).split("-")[0]
    print(f"Node: {node.name} (id={node.id})")
    print(f"Node slug: {node_slug}")
    print(f"Node host: {node.host}")
    print(f"Node wg: {node.wg_address}")
    print(f"Node domain: node-{node_slug}.{config.domain}")
    print(f"Service wildcard: *.{config.domain}")
    print(f"Service direct: *.{config.domain.replace('grid', 'grid-node' + node_slug)}")
    print()
    print("Checking services on this node:")
    from apps.deployments.models import Service, Deployment
    for svc in Service.objects.filter(status='ACTIVE'):
        dep = Deployment.objects.filter(service=svc, status='ACTIVE').order_by('-created_at').first()
        if dep and dep.target_server_id == node.id:
            slug = (svc.slug or svc.name.lower().replace(' ', '-')).strip()
            print(f"  {svc.name}: wildcard={slug}.{config.domain} direct={slug}.grid-node{node_slug}.{config.domain}")
