from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0100_volume_size_gb_range'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='previewenvironment',
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name='previewenvironment',
            unique_together={('service', 'branch_name', 'commit_sha')},
        ),
    ]
