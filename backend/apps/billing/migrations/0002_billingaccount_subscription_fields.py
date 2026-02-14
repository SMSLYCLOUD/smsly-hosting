"""Add subscription fields to BillingAccount."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingaccount",
            name="stripe_subscription_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="billingaccount",
            name="plan",
            field=models.CharField(
                choices=[
                    ("HOBBY", "Hobby"),
                    ("PRO", "Pro"),
                    ("ENTERPRISE", "Enterprise"),
                ],
                default="HOBBY",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="billingaccount",
            name="subscription_status",
            field=models.CharField(
                choices=[
                    ("NONE", "None"),
                    ("TRIALING", "Trialing"),
                    ("ACTIVE", "Active"),
                    ("PAST_DUE", "Past due"),
                    ("CANCELED", "Canceled"),
                    ("UNPAID", "Unpaid"),
                    ("INCOMPLETE", "Incomplete"),
                    ("INCOMPLETE_EXPIRED", "Incomplete expired"),
                ],
                default="NONE",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="billingaccount",
            name="current_period_end",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

