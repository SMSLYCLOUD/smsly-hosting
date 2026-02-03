"""Stripe module."""
import stripe
from django.conf import settings
from ..models import BillingAccount

stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', 'sk_test_mock')


class StripeService:
    @staticmethod
    def create_customer(user):
        customer = stripe.Customer.create(
            email=user.email,
            name=user.username
        )
        account, created = BillingAccount.objects.get_or_create(user=user)
        account.stripe_customer_id = customer.id
        account.save()
        return customer.id

    @staticmethod
    def create_checkout_session(user_id):
        account = BillingAccount.objects.get(user_id=user_id)
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='setup',
            customer=account.stripe_customer_id,
            success_url='http://localhost:3000/dashboard?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='http://localhost:3000/dashboard',
        )
        return session.url
