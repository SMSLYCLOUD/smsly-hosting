from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0008_add_verification_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="domain",
            name="verify_fail_count",
            field=models.IntegerField(default=0),
        ),
    ]
