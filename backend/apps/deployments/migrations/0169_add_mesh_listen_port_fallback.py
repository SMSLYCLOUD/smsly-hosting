from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0168_add_node_components'),
    ]

    operations = [
        migrations.AddField(
            model_name='meshnetwork',
            name='listen_port_fallback',
            field=models.IntegerField(
                default=33500,
                help_text='Fallback WireGuard port (used when primary is blocked by cloud firewall)',
            ),
        ),
    ]
