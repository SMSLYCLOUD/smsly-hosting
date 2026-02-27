from .base import PaymentProvider

class PaystackProvider(PaymentProvider):
    def create_subscription(self, plan_id, customer_email, **kwargs):
        return {"subscription_id": "sub_mock_paystack"}

    def cancel_subscription(self, subscription_id):
        return True

    def verify_webhook(self, payload, signature):
        return True
