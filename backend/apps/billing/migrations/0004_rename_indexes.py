# Fix billing index state — the 0003 migration specified custom index names
# but the database doesn't have them (table was likely created with auto-generated
# names or without indexes). This migration updates Django's internal state only,
# without touching the database.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_billingpayment'),
    ]

    operations = [
        # State-only: tell Django the indexes now use auto-generated names.
        # The database either already has these indexes or doesn't — either
        # way, we just sync Django's state to match the model definition.
        migrations.SeparateDatabaseAndState(
            state_operations=[
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
            ],
            database_operations=[],
        ),
    ]
