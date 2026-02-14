"""Urls module."""
from django.urls import path
from .views import (
    BillingSummaryView,
    CheckoutView,
    CryptomusWebhookView,
    FlutterwaveWebhookView,
    InvoicesView,
    PortalSessionView,
    StripeWebhookView,
)

urlpatterns = [
    path('summary/', BillingSummaryView.as_view(), name='billing-summary'),
    path('checkout/', CheckoutView.as_view(), name='billing-checkout'),
    path('portal/', PortalSessionView.as_view(), name='billing-portal'),
    path('invoices/', InvoicesView.as_view(), name='billing-invoices'),
    path('webhook/', StripeWebhookView.as_view(), name='billing-webhook'),
    path('flutterwave/webhook/', FlutterwaveWebhookView.as_view(), name='billing-flutterwave-webhook'),
    path('cryptomus/webhook/', CryptomusWebhookView.as_view(), name='billing-cryptomus-webhook'),
]
