from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0003_projectmember"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="organizationmembership",
            index=models.Index(fields=["user", "organization"], name="orgmember_user_org_idx"),
        ),
        migrations.AddIndex(
            model_name="projectmember",
            index=models.Index(fields=["user", "project"], name="projmember_user_proj_idx"),
        ),
    ]
