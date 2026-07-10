from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0144_alter_ecosystemplan_shared_addon_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="clusterstate",
            name="local_role",
            field=models.CharField(
                choices=[
                    ("LEADER", "Leader"),
                    ("FOLLOWER", "Follower"),
                    ("CANDIDATE", "Candidate"),
                ],
                default="FOLLOWER",
                help_text="Local server's role in the cluster",
                max_length=20,
            ),
        ),
    ]
