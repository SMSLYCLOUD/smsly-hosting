"""Billing utilities."""
import contextlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BillingAccount
from apps.billing.services.stripe import StripeService


@transaction.atomic
def _activate_paid_plan(*, user, plan: str):
    """Activate a paid plan for a user."""
    plan = (plan or "").upper().strip()
    if plan not in {
        BillingAccount.Plan.HOBBY,
        BillingAccount.Plan.PRO,
        BillingAccount.Plan.ENTERPRISE,
    }:
        return

    # Stripe remains source-of-truth for Stripe subscriptions.
    # Non-Stripe providers activate a timed period.
    account = StripeService.get_or_create_billing_account(user)
    # Lock the row for update
    account = BillingAccount.objects.select_for_update().get(id=account.id)

    account.plan = plan

    if plan == BillingAccount.Plan.PRO:
        account.subscription_status = BillingAccount.SubscriptionStatus.ACTIVE
        from apps.deployments.models_core import PlatformConfig
        days = int(PlatformConfig.get_config_value('billing_pro_period_days', '30') or 30)
        base = account.current_period_end \
            if account.current_period_end and account.current_period_end > timezone.now() \
            else timezone.now()
        account.current_period_end = base + timedelta(days=days)
    elif plan == BillingAccount.Plan.HOBBY:
        account.subscription_status = BillingAccount.SubscriptionStatus.NONE
        account.current_period_end = None
    else:
        account.subscription_status = BillingAccount.SubscriptionStatus.ACTIVE

    account.save(update_fields=["plan", "subscription_status", "current_period_end"])

    # Update Platform License
    try:
        from apps.licensing.models import PlatformLicense
        from apps.licensing.validator import validate_license

        license = PlatformLicense.load()
        # Map plan to license tier (BillingAccount.Plan -> PlatformTier)
        tier_map = {
            'PRO': 'pro',
            'ENTERPRISE': 'enterprise',
            'HOBBY': 'community'
        }
        license.tier = tier_map.get(plan, 'community')
        license.expires_at = account.current_period_end
        license.save()

        # Trigger validation to sync features if connected
        with contextlib.suppress(Exception):
            validate_license(license)

    except Exception as e:
        # Don't fail the transaction just because licensing failed (it can be retried)
        import logging
        logging.getLogger(__name__).error(f"Failed to update platform license after payment: {e}")
