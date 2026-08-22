from django.db import migrations


def assign_default_projects(apps, schema_editor):
    """Every service must belong to a project (isolation invariant).

    Orphan services predate the invariant — group them per owner into an
    auto-created 'Default' project so they inherit scoped-network isolation
    on their next deploy.
    """
    Project = apps.get_model('deployments', 'Project')
    Service = apps.get_model('deployments', 'Service')

    orphans = Service.objects.filter(project__isnull=True).exclude(owner__isnull=True)
    seen_owners = set()
    for svc in orphans.select_related('owner'):
        owner_id = svc.owner_id
        if owner_id not in seen_owners:
            project, _ = Project.objects.get_or_create(
                owner_id=owner_id,
                slug='default',
                defaults={'name': 'Default'},
            )
            seen_owners.add(owner_id)
        svc.project_id = project.id
        svc.save(update_fields=['project'])


def unassign(apps, schema_editor):
    # Not reversible: we cannot know which services were originally orphaned
    # versus deliberately assigned to 'Default'. Leave as-is.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0183_service_wildcard_internal_only'),
    ]

    operations = [
        migrations.RunPython(assign_default_projects, unassign),
    ]
