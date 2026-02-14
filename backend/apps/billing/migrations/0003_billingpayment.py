"""Create BillingPayment model."""

import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("billing", "0002_billingaccount_subscription_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingPayment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider", models.CharField(choices=[("stripe", "Stripe"), ("flutterwave", "Flutterwave"), ("cryptomus", "Cryptomus")], max_length=20)),
                ("plan", models.CharField(choices=[("HOBBY", "Hobby"), ("PRO", "Pro"), ("ENTERPRISE", "Enterprise")], default="HOBBY", max_length=20)),
                ("amount", models.DecimalField(decimal_places=2, default=0.0, max_digits=10)),
                ("currency", models.CharField(default="USD", max_length=10)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("PAID", "Paid"), ("FAILED", "Failed"), ("CANCELED", "Canceled"), ("EXPIRED", "Expired")], default="PENDING", max_length=20)),
                ("provider_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("provider_transaction_id", models.CharField(blank=True, max_length=255, null=True)),
                ("checkout_url", models.URLField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("raw_webhook", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="billing_payments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["provider", "provider_reference"], name="billing_pay_provider_ref_idx"),
                    models.Index(fields=["user", "status"], name="billing_pay_user_status_idx"),
                ],
            },
        ),
    ]

