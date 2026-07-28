from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0006_dailyrevenue_infrastructurecost"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["user", "status"], name="billing_invoice_user_status_idx"),
        ),
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["-period_end"], name="billing_invoice_period_end_idx"),
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["status", "user"], name="billing_sub_status_user_idx"),
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["stripe_subscription_id"], name="billing_sub_stripe_id_idx"),
        ),
        migrations.AddIndex(
            model_name="infrastructurecost",
            index=models.Index(fields=["date", "cost_type"], name="billing_infracost_date_type_idx"),
        ),
        migrations.AddIndex(
            model_name="usagerecord",
            index=models.Index(fields=["service", "-timestamp"], name="billing_usage_svc_ts_idx"),
        ),
    ]
