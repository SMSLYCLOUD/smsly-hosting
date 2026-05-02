from django.db import migrations

def migrate_custom_domains(apps, schema_editor):
    Service = apps.get_model('deployments', 'Service')
    Domain = apps.get_model('domains', 'Domain')

    for service in Service.objects.all():
        for domain_name in (service.custom_domains or []):
            Domain.objects.get_or_create(
                domain_name=domain_name,
                defaults={
                    'service': service,
                    'status': 'active',  # Assume active for existing domains since we don't know
                    'verified': True,
                    'ssl_active': True
                }
            )

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
