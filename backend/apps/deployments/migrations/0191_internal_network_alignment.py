from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0190_dual_homing_internal_network'),
    ]

    operations = [
        # No-op: 0190 already created the fields this commit was
        # supposed to add. Kept as a marker so future migrations
        # can depend on 'all internal-network fields are present'.
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
