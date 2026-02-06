"""Urls module."""
from django.urls import path
from .views import CheckoutView, SimulateBillingView

urlpatterns = [
    path('checkout/', CheckoutView.as_view(), name='billing-checkout'),
    path('simulate/', SimulateBillingView.as_view(), name='billing-simulate'),
]
