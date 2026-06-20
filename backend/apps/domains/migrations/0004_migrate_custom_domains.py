from django.db import migrations

BATCH_SIZE = 500


def migrate_custom_domains(apps, schema_editor):
    Service = apps.get_model('deployments', 'Service')
    Domain = apps.get_model('domains', 'Domain')

    # Pull only the fields we need to keep the working set small.
    # 'custom_domains' was a JSONField-ish list on the historical Service
    # model — iterate in batches so a large Service table doesn't blow
    # memory or hold a single long transaction.
    last_id = None
    to_create = []
    total_created = 0

    def flush():
        nonlocal to_create, total_created
        if not to_create:
            return
        # ignore_conflicts=True → ON CONFLICT DO NOTHING. The Domain table
        # has unique=True on domain_name, which mirrors the original
        # get_or_create() behaviour: existing rows are returned untouched,
        # never overwritten.
        Domain.objects.bulk_create(to_create, ignore_conflicts=True)
        total_created += len(to_create)
        to_create = []

    while True:
        qs = Service.objects.exclude(custom_domains__isnull=True)
        if last_id is not None:
            qs = qs.filter(id__gt=last_id)
        batch = list(qs.order_by('id').only('id', 'custom_domains')[:BATCH_SIZE])
        if not batch:
            break

        for service in batch:
            custom_domains = service.custom_domains or []
            for domain_name in custom_domains:
                if not domain_name:
                    continue
                to_create.append(
                    Domain(
                        domain_name=domain_name,
                        service=service,
                        status='active',  # Assume active for existing domains
                        verified=True,
                        ssl_active=True,
                    )
                )
            last_id = service.id

        if len(to_create) >= BATCH_SIZE:
            flush()

    flush()

    if total_created:
        print(f"Migrated {total_created} custom domain(s) into the Domain table.")


def reverse_migrate_custom_domains(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('domains', '0003_domain_dns_actual_domain_dns_expected_and_more'),
        ('deployments', '0055_managedserver_private_ip_and_more')
    ]

    operations = [
        migrations.RunPython(migrate_custom_domains, reverse_migrate_custom_domains),
    ]

