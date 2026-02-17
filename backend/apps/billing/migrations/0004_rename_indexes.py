# Generated migration for billing index renames
# Django auto-detected that the custom index names from 0003 need renaming
# to match Django's default naming convention.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_billingpayment'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='billingpayment',
            new_name='billing_bil_provide_560a86_idx',
            old_name='billing_pay_provider_ref_idx',
        ),
        migrations.RenameIndex(
            model_name='billingpayment',
            new_name='billing_bil_user_id_2d66af_idx',
            old_name='billing_pay_user_status_idx',
        ),
    ]
