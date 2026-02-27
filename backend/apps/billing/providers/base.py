from abc import ABC, abstractmethod

class PaymentProvider(ABC):
    @abstractmethod
    def create_subscription(self, plan_id, customer_email, **kwargs):
        pass

    @abstractmethod
    def cancel_subscription(self, subscription_id):
        pass

    @abstractmethod
    def verify_webhook(self, payload, signature):
        pass
