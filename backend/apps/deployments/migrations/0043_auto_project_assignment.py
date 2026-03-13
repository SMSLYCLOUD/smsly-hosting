from django.db import migrations

def assign_default_projects(apps, schema_editor):
    Project = apps.get_model('deployments', 'Project')
    Service = apps.get_model('deployments', 'Service')
    Addon = apps.get_model('deployments', 'Addon')
    MeshNetwork = apps.get_model('deployments', 'MeshNetwork')
    ManagedServer = apps.get_model('deployments', 'ManagedServer')

    # 1. Services
    for service in Service.objects.filter(project__isnull=True):
        if service.owner:
            project, _ = Project.objects.get_or_create(
                owner=service.owner,
                name='Default',
                defaults={'slug': 'default', 'is_default': True}
            )
            service.project = project
            service.save()

    # 2. Addons (inherit project from service)
    for addon in Addon.objects.filter(project__isnull=True, service__isnull=False, service__project__isnull=False):
        addon.project = addon.service.project
        addon.save()

    # 3. ManagedServers (they have owner)
    for server in ManagedServer.objects.filter(owner__isnull=False):
        project, _ = Project.objects.get_or_create(
            owner=server.owner,
            name='Default',
            defaults={'slug': 'default', 'is_default': True}
        )
        server.project = project
        server.save()

    # 4. MeshNetwork - typically attached to a project but they don't have an owner field directly.
    # Try linking to the first project available if needed, or leave null.
    # Usually they should be left null or handled when created.
    # We will leave them null to avoid guessing.

def reverse_assignment(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0042_addon_project_managedserver_project_and_more'),
    ]

    operations = [
        migrations.RunPython(assign_default_projects, reverse_assignment),
    ]
