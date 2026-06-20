from .base import PaymentProvider


class StripeProvider(PaymentProvider):
    def create_subscription(self, plan_id, customer_email, **kwargs):
        raise NotImplementedError(
            "Legacy StripeProvider is not wired. Use apps.billing.services.stripe.StripeService."
        )

    def cancel_subscription(self, subscription_id):
        raise NotImplementedError(
            "Legacy StripeProvider is not wired. Use apps.billing.services.stripe.StripeService."
        )

    def verify_webhook(self, payload, signature):
        raise NotImplementedError(
            "Legacy StripeProvider is not wired. Use apps.billing.services.stripe.StripeService."
        )
