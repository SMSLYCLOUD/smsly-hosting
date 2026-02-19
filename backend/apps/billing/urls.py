"""Urls module."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    BillingSummaryView,
    CheckoutView,
    CryptomusWebhookView,
    FlutterwaveWebhookView,
    InvoicesView,
    PortalSessionView,
    StripeWebhookView,
    PricingPlanViewSet,
    SubscriptionViewSet,
    InvoiceViewSet,
    UsageViewSet,
    AdminPricingPlanViewSet,
    AdminResourcePriceViewSet
)
from .views_analytics import AnalyticsViewSet

router = DefaultRouter()
router.register(r'plans', PricingPlanViewSet)
router.register(r'subscription', SubscriptionViewSet, basename='subscription')
router.register(r'usage', UsageViewSet, basename='usage')
router.register(r'admin/plans', AdminPricingPlanViewSet, basename='admin-plans')
router.register(r'admin/resource-prices', AdminResourcePriceViewSet, basename='admin-resource-prices')
router.register(r'admin/analytics', AnalyticsViewSet, basename='admin-analytics')

# Separate router for invoices to avoid conflict or just replace?
router.register(r'invoices', InvoiceViewSet, basename='invoices')

urlpatterns = [
    path('', include(router.urls)),
    path('summary/', BillingSummaryView.as_view(), name='billing-summary'),
    path('checkout/', CheckoutView.as_view(), name='billing-checkout'),
    path('portal/', PortalSessionView.as_view(), name='billing-portal'),
    path('webhook/', StripeWebhookView.as_view(), name='billing-webhook'),
    path('flutterwave/webhook/', FlutterwaveWebhookView.as_view(), name='billing-flutterwave-webhook'),
    path('cryptomus/webhook/', CryptomusWebhookView.as_view(), name='billing-cryptomus-webhook'),
]
