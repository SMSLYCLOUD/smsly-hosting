"""Stripe module."""
import stripe
from django.conf import settings
from apps.billing.models import BillingAccount

stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', 'sk_test_mock')


class StripeService:
    @staticmethod
    def create_customer(user):
        customer = stripe.Customer.create(
            email=user.email,
            name=user.username
        )
        account, _ = BillingAccount.objects.get_or_create(user=user)
        account.stripe_customer_id = customer.id
        account.save()
        return customer.id

    @staticmethod
    def create_checkout_session(user_id):
        account = BillingAccount.objects.get(user_id=user_id)
        
        # Determine Base URL (MVP: Hardcoded for now, should be settings.SITE_URL)
        base_url = getattr(settings, 'SITE_URL', 'http://localhost:3000')

        if settings.STRIPE_SECRET_KEY.startswith('sk_live_') or settings.STRIPE_SECRET_KEY.startswith('sk_test_'):
            # REAL STRIPE MODE
            try:
                session = stripe.checkout.Session.create(
                    payment_method_types=['card'],
                    mode='setup',
                    customer=account.stripe_customer_id,
                    success_url=f'{base_url}/settings/billing?session_id={{CHECKOUT_SESSION_ID}}',
                    cancel_url=f'{base_url}/settings/billing',
                )
                return session.url
            except Exception as e:
                # Log error and fall back to mock if configured, or re-raise
                print(f"Stripe Error: {e}")
                if not settings.DEBUG:
                    raise e
        
        # MOCK MODE (Dev/Demo)
        return f'{base_url}/settings/billing?mock_success=true'
