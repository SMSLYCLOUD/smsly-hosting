from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0007_add_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["status", "due_date"], name="billing_invoice_status_due_idx"),
        ),
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["status", "paid_at"], name="billing_invoice_status_paid_idx"),
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["status", "current_period_end"], name="billing_sub_status_period_end_idx"),
        ),
    ]
