from .base import PaymentProvider


class PaystackProvider(PaymentProvider):
    def create_subscription(self, plan_id, customer_email, **kwargs):
        raise NotImplementedError(
            "Legacy PaystackProvider is not wired to a live backend integration."
        )

    def cancel_subscription(self, subscription_id):
        raise NotImplementedError(
            "Legacy PaystackProvider is not wired to a live backend integration."
        )

    def verify_webhook(self, payload, signature):
        raise NotImplementedError(
            "Legacy PaystackProvider is not wired to a live backend integration."
        )
