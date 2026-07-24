"""Stripe helpers for subscriptions and customer portal."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import stripe
from apps.billing.models import BillingAccount
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StripeInvoice:
    id: str
    status: str
    amount_paid: int
    currency: str
    hosted_invoice_url: str | None = None
    invoice_pdf: str | None = None
    created: int | None = None


class StripeService:
    @staticmethod
    def is_configured() -> bool:
        key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
        return key.strip().startswith("sk_")

    @staticmethod
    def _configure_stripe():
        if not StripeService.is_configured():
            raise ValueError("Stripe is not configured (STRIPE_SECRET_KEY missing).")
        stripe.api_key = settings.STRIPE_SECRET_KEY

    @staticmethod
    def _get_price_id_for_plan(plan: str) -> str:
        plan = (plan or "").upper().strip()
        if plan == BillingAccount.Plan.PRO:
            price_id = getattr(settings, "STRIPE_PRICE_PRO", "") or ""
            if not price_id:
                raise ValueError("Stripe price not configured for PRO (STRIPE_PRICE_PRO missing).")
            return price_id
        if plan == BillingAccount.Plan.ENTERPRISE:
            # Typically handled via sales-assisted invoicing.
            raise ValueError("Enterprise plan requires sales assistance.")
        if plan == BillingAccount.Plan.HOBBY:
            raise ValueError("Hobby is free. No checkout is required.")
        raise ValueError("Unknown plan.")

    @staticmethod
    def get_or_create_billing_account(user) -> BillingAccount:
        account, _ = BillingAccount.objects.get_or_create(user=user)
        return account

    @staticmethod
    def ensure_customer(user) -> BillingAccount:
        StripeService._configure_stripe()

        account = StripeService.get_or_create_billing_account(user)
        if account.stripe_customer_id:
            return account

        email_value = getattr(user, "email", "") or ""
        name_value = getattr(user, "username", "") or ""
        customer = stripe.Customer.create(
            email=email_value or "",
            name=name_value or "",
            metadata={"user_id": str(getattr(user, "id", ""))},
        )
        account.stripe_customer_id = customer.id
        account.save(update_fields=["stripe_customer_id"])
        return account

    @staticmethod
    def create_subscription_checkout_session(
        *,
        user,
        plan: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        StripeService._configure_stripe()

        plan = (plan or "").upper().strip()
        price_id = StripeService._get_price_id_for_plan(plan)

        account = StripeService.ensure_customer(user)

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer=account.stripe_customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            allow_promotion_codes=True,
            client_reference_id=str(getattr(user, "id", "")),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"plan": plan},
        )
        return session.url or ""

    @staticmethod
    def create_portal_session(*, user, return_url: str) -> str:
        StripeService._configure_stripe()
        account = StripeService.ensure_customer(user)

        session = stripe.billing_portal.Session.create(
            customer=account.stripe_customer_id,
            return_url=return_url,
        )
        return session.url

    @staticmethod
    def list_invoices(*, user, limit: int = 10) -> list[StripeInvoice]:
        StripeService._configure_stripe()
        account = StripeService.ensure_customer(user)

        invoices = stripe.Invoice.list(customer=account.stripe_customer_id, limit=limit)
        out: list[StripeInvoice] = []
        for inv in invoices.data or []:
            out.append(
                StripeInvoice(
                    id=inv.get("id"),
                    status=inv.get("status"),
                    amount_paid=inv.get("amount_paid"),
                    currency=inv.get("currency"),
                    hosted_invoice_url=inv.get("hosted_invoice_url"),
                    invoice_pdf=inv.get("invoice_pdf"),
                    created=inv.get("created"),
                )
            )
        return out

    @staticmethod
    def sync_subscription_from_stripe(account: BillingAccount) -> BillingAccount:
        """
        Fetch subscription status from Stripe and persist it.

        Safe to call when Stripe is configured; otherwise no-op.
        """
        if not StripeService.is_configured():
            return account

        if not account.stripe_customer_id or not account.stripe_subscription_id:
            return account

        StripeService._configure_stripe()
        try:
            sub = stripe.Subscription.retrieve(account.stripe_subscription_id)
        except Exception as e:
            logger.warning("Stripe subscription retrieve failed: %s", e)
            return account

        status = (sub.get("status") or "").upper()
        period_end = sub.get("current_period_end")
        account.subscription_status = status if status else BillingAccount.SubscriptionStatus.NONE
        if isinstance(period_end, int):
            account.current_period_end = timezone.datetime.fromtimestamp(
                period_end, tz=timezone.get_current_timezone()
            )
        account.save(update_fields=["subscription_status", "current_period_end"])
        return account

