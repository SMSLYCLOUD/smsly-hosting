from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0006_teammember_can_manage_billing_teammember_expires_at_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="teammember",
            index=models.Index(fields=["user", "team"], name="teammember_user_team_idx"),
        ),
        migrations.AddIndex(
            model_name="teammember",
            index=models.Index(fields=["user", "is_active"], name="teammember_user_active_idx"),
        ),
        migrations.AddIndex(
            model_name="team",
            index=models.Index(fields=["owner"], name="team_owner_idx"),
        ),
    ]
