from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0037_election_models_server_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='servertransfer',
            name='target_ssh_password',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
