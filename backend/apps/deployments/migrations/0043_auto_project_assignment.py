from django.db import migrations


BATCH_SIZE = 500


def _assign_owner_projects(model, apps, schema_editor, owner_id_field):
    """Shared helper: for any owner-keyed model, get/create one 'Default'
    project per owner, then batch-update the model rows to point at it.

    `owner_id_field` is the FK column on the source model
    (e.g. Service.owner, ManagedServer.owner).
    """
    Project = apps.get_model('deployments', 'Project')
    Source = apps.get_model('deployments', model)

    # 1. Collect owner_ids that still need a default project.
    owner_ids = list(
        Source.objects.filter(project__isnull=True)
        .exclude(**{f'{owner_id_field}__isnull': True})
        .values_list(f'{owner_id_field}_id', flat=True)
        .distinct()
    )
    if not owner_ids:
        return

    # 2. Fetch existing default projects for those owners in one query.
    existing = {
        p.owner_id: p
        for p in Project.objects.filter(
            owner_id__in=owner_ids, name='Default'
        )
    }

    # 3. Bulk-create the missing projects. ignore_conflicts skips owners
    # that already have a default project (e.g. created concurrently).
    missing = [oid for oid in owner_ids if oid not in existing]
    if missing:
        Project.objects.bulk_create(
            [
                Project(
                    owner_id=oid,
                    name='Default',
                    slug='default',
                    is_default=True,
                )
                for oid in missing
            ],
            ignore_conflicts=True,
        )
        # Re-fetch to pick up DB-assigned PKs for the rows we just inserted
        # (ignore_conflicts on Postgres doesn't return IDs).
        existing = {
            p.owner_id: p
            for p in Project.objects.filter(
                owner_id__in=owner_ids, name='Default'
            )
        }

    owner_to_project_id = {oid: p.id for oid, p in existing.items()}

    # 4. Batch-update source rows in chunks of BATCH_SIZE.
    last_id = None
    while True:
        qs = Source.objects.filter(project__isnull=True)
        if last_id is not None:
            qs = qs.filter(id__gt=last_id)
        batch = list(qs.order_by('id')[:BATCH_SIZE])
        if not batch:
            break

        to_update = []
        for row in batch:
            owner_id = getattr(row, f'{owner_id_field}_id')
            if owner_id in owner_to_project_id:
                row.project_id = owner_to_project_id[owner_id]
                to_update.append(row)
            last_id = row.id

        if to_update:
            Source.objects.bulk_update(to_update, ['project'])


def assign_default_projects(apps, schema_editor):
    connection = schema_editor.connection

    # 1. Services: per-owner default project.
    _assign_owner_projects('Service', apps, schema_editor, 'owner')

    # 2. Addons: single SQL UPDATE — inherit project_id from the parent
    # service. No Python loop, no per-row save.
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE deployments_addon a
                SET project_id = s.project_id
                FROM deployments_service s
                WHERE a.service_id = s.id
                  AND a.project_id IS NULL
                  AND s.project_id IS NOT NULL
                """
            )
    else:
        # SQLite/MySQL fallback — emulate the JOIN with a correlated subquery.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE deployments_addon
                SET project_id = (
                    SELECT project_id FROM deployments_service
                    WHERE deployments_service.id = deployments_addon.service_id
                )
                WHERE project_id IS NULL
                  AND service_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM deployments_service
                    WHERE deployments_service.id = deployments_addon.service_id
                      AND deployments_service.project_id IS NOT NULL
                  )
                """
            )

    # 3. ManagedServers: per-owner default project (same as services).
    _assign_owner_projects('ManagedServer', apps, schema_editor, 'owner')


def reverse_assignment(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0042_addon_project_managedserver_project_and_more'),
    ]

    operations = [
        migrations.RunPython(assign_default_projects, reverse_assignment),
    ]

