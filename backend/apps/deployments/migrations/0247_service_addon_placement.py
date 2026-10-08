# Generated 2026-10-08: per-service addon backend placement (AUTO/MASTER/NODE).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0246_addon_provider_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='addon_placement',
            field=models.CharField(
                choices=[('AUTO', 'Auto'), ('MASTER', 'Master'), ('NODE', 'Node')],
                default='AUTO',
                help_text="Where this service's addon backends live. AUTO keeps the platform default (remote full-stack services provision on their node, everything else on master). MASTER pins backends to master with mesh reachability; NODE pins them to the service's node. Only meaningful for services on a remote full-stack node.",
                max_length=10,
            ),
        ),
    ]
