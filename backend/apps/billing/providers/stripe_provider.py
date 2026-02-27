from .base import PaymentProvider

class StripeProvider(PaymentProvider):
    def create_subscription(self, plan_id, customer_email, **kwargs):
        # Placeholder
        return {"subscription_id": "sub_mock", "client_secret": "secret_mock"}

    def cancel_subscription(self, subscription_id):
        return True

    def verify_webhook(self, payload, signature):
        return True
