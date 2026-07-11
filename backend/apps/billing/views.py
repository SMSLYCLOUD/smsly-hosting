"""Billing API views (Stripe checkout + portal + usage summary)."""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from apps.billing.models import (
    BillingAccount,
    BillingPayment,
    Invoice,
    PricingPlan,
    ResourcePrice,
    UsageRecord,
    UserSubscription,
)
from apps.billing.serializers import (
    InvoiceSerializer,
    PricingPlanSerializer,
    ResourcePriceSerializer,
    UserSubscriptionSerializer,
)
from apps.billing.services.cryptomus import CryptomusService
from apps.billing.services.flutterwave import FlutterwaveService
from apps.billing.services.metering import UsageMeter
from apps.billing.services.stripe import StripeService
from apps.billing.utils import _activate_paid_plan
from apps.deployments.models_core import PlatformConfig
from apps.permissions.drf import CanManageBilling, CanViewBilling

logger = logging.getLogger(__name__)


class CheckoutSerializer(serializers.Serializer):
    """Serializer for checkout requests."""
    plan = serializers.ChoiceField(
        choices=[
            BillingAccount.Plan.HOBBY,
            BillingAccount.Plan.PRO,
            BillingAccount.Plan.ENTERPRISE,
        ]
    )
    provider = serializers.ChoiceField(
        choices=[
            BillingPayment.Provider.STRIPE,
            BillingPayment.Provider.FLUTTERWAVE,
            BillingPayment.Provider.CRYPTOMUS,
        ],
        required=False,
        allow_blank=True,
    )

    def create(self, validated_data):
        """Not used."""
        return validated_data

    def update(self, instance, validated_data):
        """Not used."""
        return instance


class PortalSerializer(serializers.Serializer):
    """Serializer for portal session requests."""
    return_url = serializers.URLField(required=False, allow_blank=True)

    def create(self, validated_data):
        """Not used."""
        return validated_data

    def update(self, instance, validated_data):
        """Not used."""
        return instance


class EmptySerializer(serializers.Serializer):
    """Schema placeholder for response-only APIViews."""


def _base_url_from_request(request) -> str:
    """Extract base URL from request."""
    # build_absolute_uri("/") returns something like "https://cloud.smsly.cloud/"
    base = request.build_absolute_uri("/")
    return base[:-1] if base.endswith("/") else base


def _choose_provider(provider_in: str | None) -> str:
    """Select the active billing provider."""
    p = (provider_in or "").strip().lower()
    if p:
        return p
    if StripeService.is_configured():
        return BillingPayment.Provider.STRIPE
    if FlutterwaveService.is_configured():
        return BillingPayment.Provider.FLUTTERWAVE
    if CryptomusService.is_configured():
        return BillingPayment.Provider.CRYPTOMUS
    return BillingPayment.Provider.STRIPE


def _pro_amount_currency() -> tuple[Decimal, str]:
    """Get the pro plan amount and currency."""
    from apps.deployments.models_core import PlatformConfig
    try:
        amount = Decimal(PlatformConfig.get_config_value('billing_pro_amount', '29.00'))
    except Exception as e:
        raise ValueError("Invalid billing_pro_amount") from e
    currency = PlatformConfig.get_config_value('billing_currency', 'USD').upper().strip()
    return amount, currency


class BillingSummaryView(GenericAPIView):
    """View for retrieving billing summary."""
    serializer_class = EmptySerializer
    permission_classes = [IsAuthenticated, CanViewBilling]

    def get(self, request):
        """Get billing summary."""
        user = request.user

        account = StripeService.get_or_create_billing_account(user)
        # Best-effort sync so the UI reflects Stripe changes even if webhooks are delayed.
        try:
            account = StripeService.sync_subscription_from_stripe(account)
        except Exception: # pylint: disable=broad-exception-caught
            pass

        total_cost = (
            UsageRecord.objects.filter(service__owner=user)
            .aggregate(total=Sum("cost"))["total"] or Decimal("0.00")
        )

        services_out = []
        for service in user.services.all():
            service_cost = (
                service.usage_records.aggregate(total=Sum("cost"))["total"] or Decimal("0.00")
            )
            cpu_hours = service.usage_records.filter(
                resource_type='cpu_hours'
            ).aggregate(s=Sum('quantity'))['s'] or 0
            services_out.append(
                {
                    "id": str(service.id),
                    "name": service.name,
                    "cost": float(service_cost),
                    "cpu_usage_hours": float(cpu_hours),
                }
            )

        return Response(
            {
                "currency": PlatformConfig.get_config_value('billing_currency', 'USD').upper(),
                "stripe_configured": StripeService.is_configured(),
                "flutterwave_configured": FlutterwaveService.is_configured(),
                "cryptomus_configured": CryptomusService.is_configured(),
                "plan": account.plan,
                "subscription_status": account.subscription_status,
                "current_period_end": account.current_period_end.isoformat()
                if account.current_period_end
                else None,
                "balance": float(account.balance),
                "total_estimated_cost": float(total_cost),
                "billing_period": "Current Month",
                "services": services_out,
            }
        )


class CheckoutView(GenericAPIView):
    """View for initiating checkout."""
    serializer_class = CheckoutSerializer
    permission_classes = [IsAuthenticated, CanManageBilling]

    def post(self, request):
        """Create checkout session."""
        # pylint: disable=too-many-locals, too-many-branches
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        plan = serializer.validated_data["plan"]
        provider = _choose_provider(serializer.validated_data.get("provider"))
        base = _base_url_from_request(request)

        try:
            if provider == BillingPayment.Provider.STRIPE:
                success_url = (f"{base}/billing?checkout=success&provider=stripe"
                               f"&session_id={{CHECKOUT_SESSION_ID}}")
                cancel_url = f"{base}/billing?checkout=cancelled&provider=stripe"
                url = StripeService.create_subscription_checkout_session(
                    user=request.user,
                    plan=plan,
                    success_url=success_url,
                    cancel_url=cancel_url,
                )
                return Response({"url": url})

            if plan != BillingAccount.Plan.PRO:
                raise ValueError("Only PRO plan checkout is supported for this provider.")

            amount, currency = _pro_amount_currency()

            if provider == BillingPayment.Provider.FLUTTERWAVE:
                if not FlutterwaveService.is_configured():
                    raise ValueError("Flutterwave is not configured.")
                tx_ref = f"cn_fw_{uuid.uuid4().hex}"
                payment = BillingPayment.objects.create(
                    user=request.user,
                    provider=BillingPayment.Provider.FLUTTERWAVE,
                    plan=plan,
                    amount=amount,
                    currency=currency,
                    status=BillingPayment.Status.PENDING,
                    provider_reference=tx_ref,
                    metadata={"plan": plan},
                )
                redirect_url = (f"{base}/billing?checkout=returned&provider=flutterwave"
                                f"&tx_ref={tx_ref}")
                link = FlutterwaveService.create_payment_link(
                    user=request.user,
                    tx_ref=tx_ref,
                    amount=amount,
                    currency=currency,
                    redirect_url=redirect_url,
                    title="Grid Pro",
                    description="Upgrade to Grid Pro",
                    meta={
                        "user_id": str(request.user.id),
                        "plan": plan,
                        "payment_id": str(payment.id)
                    },
                )
                payment.checkout_url = link
                payment.save(update_fields=["checkout_url"])
                return Response({"url": link})

            if provider == BillingPayment.Provider.CRYPTOMUS:
                if not CryptomusService.is_configured():
                    raise ValueError("Cryptomus is not configured.")
                order_id = f"cn_cm_{uuid.uuid4().hex}"
                payment = BillingPayment.objects.create(
                    user=request.user,
                    provider=BillingPayment.Provider.CRYPTOMUS,
                    plan=plan,
                    amount=amount,
                    currency=currency,
                    status=BillingPayment.Status.PENDING,
                    provider_reference=order_id,
                    metadata={"plan": plan},
                )
                url_return = (f"{base}/billing?checkout=returned&provider=cryptomus"
                              f"&order_id={order_id}")
                url_callback = f"{base}/api/v1/billing/cryptomus/webhook/"
                link = CryptomusService.create_invoice(
                    order_id=order_id,
                    amount=amount,
                    currency=currency,
                    url_return=url_return,
                    url_callback=url_callback,
                    additional_data=json.dumps({
                        "user_id": str(request.user.id),
                        "plan": plan,
                        "payment_id": str(payment.id)
                    }),
                )
                payment.checkout_url = link
                payment.save(update_fields=["checkout_url"])
                return Response({"url": link})

            raise ValueError("Unknown provider.")
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Checkout session creation failed")
            return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)


class PortalSessionView(GenericAPIView):
    """View for creating customer portal sessions."""
    serializer_class = PortalSerializer
    permission_classes = [IsAuthenticated, CanManageBilling]

    def post(self, request):
        """Create portal session."""
        serializer = PortalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        base = _base_url_from_request(request)
        return_url = serializer.validated_data.get("return_url") or f"{base}/billing"

        try:
            url = StripeService.create_portal_session(user=request.user, return_url=return_url)
            return Response({"url": url})
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Portal session creation failed")
            return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)


class InvoicesView(GenericAPIView):
    """View for listing user invoices."""
    serializer_class = EmptySerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List invoices."""
        if not StripeService.is_configured():
            return Response({"invoices": []})

        try:
            invoices = StripeService.list_invoices(user=request.user, limit=10)
            return Response({"invoices": invoices})
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Invoice listing failed")
            return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)


class WebhookRateThrottle(AnonRateThrottle):
    rate = '60/minute'


class StripeWebhookView(GenericAPIView):
    """
    Stripe webhook endpoint.

    SECURITY:
    - Verifies signatures when STRIPE_WEBHOOK_SECRET is configured.
    - In production (DEBUG=False), a missing secret is treated as misconfiguration.
    """

    serializer_class = EmptySerializer
    permission_classes = [AllowAny]
    throttle_classes = [WebhookRateThrottle]

    def post(self, request):
        # pylint: disable=too-many-locals, too-many-statements, too-many-return-statements, too-many-branches
        """Handle webhook."""
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""

        if not secret:
            if not settings.DEBUG:
                return Response(
                    {"error": "STRIPE_WEBHOOK_SECRET is not configured"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Dev-mode only: allow unsigned payloads.
            try:
                event = json.loads(payload.decode("utf-8"))
            except Exception: # pylint: disable=broad-exception-caught
                return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            try:
                # pylint: disable=protected-access
                StripeService._configure_stripe()
                event = stripe.Webhook.construct_event(payload, sig_header, secret)
            except Exception: # pylint: disable=broad-exception-caught
                logger.exception("Stripe webhook verification failed")
                return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)

        event_type = event.get("type")
        obj = (event.get("data") or {}).get("object") or {}

        def norm_status(s: str | None) -> str:
            return (s or "").upper().strip()

        try:
            if event_type == "checkout.session.completed":
                customer_id = obj.get("customer")
                subscription_id = obj.get("subscription")
                plan = ((obj.get("metadata") or {}).get("plan") or "").upper().strip()

                if customer_id:
                    with transaction.atomic():
                        account = BillingAccount.objects.select_for_update().filter(
                            stripe_customer_id=customer_id
                        ).first()
                        if account:
                            if subscription_id:
                                account.stripe_subscription_id = subscription_id
                            if plan in {
                                BillingAccount.Plan.HOBBY,
                                BillingAccount.Plan.PRO,
                                BillingAccount.Plan.ENTERPRISE,
                            }:
                                account.plan = plan
                            account.save(update_fields=["stripe_subscription_id", "plan"])
                            StripeService.sync_subscription_from_stripe(account)

            elif event_type in {"customer.subscription.updated", "customer.subscription.deleted"}:
                subscription_id = obj.get("id")
                customer_id = obj.get("customer")
                status_up = norm_status(obj.get("status"))
                period_end = obj.get("current_period_end")

                with transaction.atomic():
                    account = None
                    if subscription_id:
                        account = BillingAccount.objects.select_for_update().filter(
                            stripe_subscription_id=subscription_id
                        ).first()
                    if not account and customer_id:
                        account = BillingAccount.objects.select_for_update().filter(
                            stripe_customer_id=customer_id
                        ).first()

                    if account:
                        if status_up:
                            account.subscription_status = status_up
                        if isinstance(period_end, int):
                            account.current_period_end = timezone.datetime.fromtimestamp(
                                period_end, tz=timezone.get_current_timezone()
                            )
                        account.save(update_fields=["subscription_status", "current_period_end"])

            # Ignore other events (invoice.* etc) for now.
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Stripe webhook processing failed: %s", e)
            return Response(
                {"error": "Webhook processing failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({"ok": True})


class FlutterwaveWebhookView(GenericAPIView):
    """
    Flutterwave webhook endpoint.

    SECURITY:
    - Verifies `verif-hash` (preferred) or `flutterwave-signature`
      using FLUTTERWAVE_WEBHOOK_SECRET_HASH.
    - In production (DEBUG=False), missing secret hash fails closed.
    """

    serializer_class = EmptySerializer
    permission_classes = [AllowAny]
    throttle_classes = [WebhookRateThrottle]

    def post(self, request):
        # pylint: disable=too-many-return-statements
        """Handle webhook."""
        raw = request.body
        headers = {
            "verif-hash": request.META.get("HTTP_VERIF_HASH", ""),
            "flutterwave-signature": request.META.get("HTTP_FLUTTERWAVE_SIGNATURE", ""),
        }

        try:
            if not FlutterwaveService.verify_webhook_signature(raw_body=raw, headers=headers):
                return Response(
                    {"error": "Invalid signature"},
                    status=status.HTTP_401_UNAUTHORIZED
                )
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Flutterwave webhook signature verification failed")
            return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            event = FlutterwaveService.parse_webhook(raw)
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Flutterwave webhook parsing failed")
            return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)

        data = (event.get("data") or {}) if isinstance(event, dict) else {}
        tx_ref = data.get("tx_ref") or data.get("txRef")
        fw_status = (data.get("status") or "").lower().strip()
        transaction_id = data.get("id") or data.get("transaction_id") or data.get("flw_ref")

        if not tx_ref:
            return Response({"error": "tx_ref missing"}, status=status.HTTP_400_BAD_REQUEST)

        payment = BillingPayment.objects.filter(
            provider=BillingPayment.Provider.FLUTTERWAVE,
            provider_reference=tx_ref,
        ).first()

        if not payment:
            logger.warning("Flutterwave webhook for unknown tx_ref=%s", tx_ref)
            return Response({"ok": True})

        with transaction.atomic():
            payment = BillingPayment.objects.select_for_update().filter(id=payment.id).first()
            if not payment or payment.status == BillingPayment.Status.PAID:
                return Response({"ok": True})

            payment.raw_webhook = event
            if transaction_id is not None:
                payment.provider_transaction_id = str(transaction_id)

            if fw_status in {"successful", "success"}:
                payment.status = BillingPayment.Status.PAID
                payment.save()
                _activate_paid_plan(user=payment.user, plan=payment.plan)
            elif fw_status in {"failed"}:
                payment.status = BillingPayment.Status.FAILED
                payment.save()
            else:
                payment.status = BillingPayment.Status.PENDING
                payment.save()

        return Response({"ok": True})


class CryptomusWebhookView(GenericAPIView):
    """
    Cryptomus webhook endpoint.

    SECURITY:
    - Verifies payload `sign` using CRYPTOMUS_API_KEY (md5(base64(json_without_sign)+api_key)).
    """

    serializer_class = EmptySerializer
    permission_classes = [AllowAny]
    throttle_classes = [WebhookRateThrottle]

    def post(self, request):
        # pylint: disable=too-many-return-statements
        """Handle webhook."""
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception: # pylint: disable=broad-exception-caught
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if not CryptomusService.verify_webhook(payload=payload):
                return Response(
                    {"error": "Invalid signature"},
                    status=status.HTTP_401_UNAUTHORIZED
                )
        except Exception: # pylint: disable=broad-exception-caught
            logger.exception("Cryptomus webhook verification failed")
            return Response({"error": "A billing error occurred. Please try again or contact support."}, status=status.HTTP_400_BAD_REQUEST)

        order_id = payload.get("order_id") or payload.get("orderId")
        cm_status = (payload.get("status") or "").lower().strip()
        transaction_id = (payload.get("uuid") or
                          payload.get("payment_id") or
                          payload.get("paymentId"))

        if not order_id:
            return Response({"error": "order_id missing"}, status=status.HTTP_400_BAD_REQUEST)

        payment = BillingPayment.objects.filter(
            provider=BillingPayment.Provider.CRYPTOMUS,
            provider_reference=order_id,
        ).first()

        if not payment:
            logger.warning("Cryptomus webhook for unknown order_id=%s", order_id)
            return Response({"ok": True})

        with transaction.atomic():
            payment = BillingPayment.objects.select_for_update().filter(id=payment.id).first()
            if not payment or payment.status == BillingPayment.Status.PAID:
                return Response({"ok": True})

            payment.raw_webhook = payload
            if transaction_id is not None:
                payment.provider_transaction_id = str(transaction_id)

            if cm_status in {"paid", "paid_over"}:
                payment.status = BillingPayment.Status.PAID
                payment.save()
                _activate_paid_plan(user=payment.user, plan=payment.plan)
            elif cm_status in {"expired"}:
                payment.status = BillingPayment.Status.EXPIRED
                payment.save()
            elif cm_status in {"cancel", "canceled"}:
                payment.status = BillingPayment.Status.CANCELED
                payment.save()
            elif cm_status in {"fail", "failed", "wrong_amount"}:
                payment.status = BillingPayment.Status.FAILED
                payment.save()
            else:
                payment.status = BillingPayment.Status.PENDING
                payment.save()

        return Response({"ok": True})


class PricingPlanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PricingPlan.objects.filter(is_active=True).order_by('sort_order')
    serializer_class = PricingPlanSerializer
    permission_classes = [AllowAny]


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = UserSubscription.objects.all()
    serializer_class = UserSubscriptionSerializer
    permission_classes = [IsAuthenticated, CanViewBilling]

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    @action(detail=False, methods=['POST'])
    def subscribe(self, request):
        """Backward-compatible alias for /billing/checkout/."""
        if not CanManageBilling().has_permission(request, self):
            return Response({'error': 'You do not have billing management access'}, status=status.HTTP_403_FORBIDDEN)
        checkout_view = CheckoutView()
        return checkout_view.post(request)

    @action(detail=False, methods=['POST'])
    def cancel(self, request):
        if not CanManageBilling().has_permission(request, self):
            return Response({'error': 'You do not have billing management access'}, status=status.HTTP_403_FORBIDDEN)
        sub = self.get_queryset().filter(status='ACTIVE').first()
        if not sub:
            return Response({'error': 'No active subscription'}, status=status.HTTP_400_BAD_REQUEST)
        # Cancel Stripe subscription if one exists.
        try:
            from apps.billing.services.stripe import StripeService
            if sub.user.billing_account.stripe_subscription_id:
                StripeService.cancel_subscription(sub.user)
        except Exception as e:
            logger.warning("Failed to cancel Stripe subscription for %s: %s", sub.user, e)
        sub.status = 'CANCELLED'
        sub.save()
        return Response({'status': 'Subscription cancelled'})


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated, CanViewBilling]

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user).order_by('-period_end')


class UsageSummarySerializer(serializers.Serializer):
    cpu_hours = serializers.DecimalField(max_digits=20, decimal_places=6)
    memory_gb_hours = serializers.DecimalField(max_digits=20, decimal_places=6)
    storage_gb = serializers.DecimalField(max_digits=20, decimal_places=6)
    bandwidth_gb = serializers.DecimalField(max_digits=20, decimal_places=6)
    active_services = serializers.IntegerField()
    active_addons = serializers.IntegerField()


class UsageViewSet(viewsets.GenericViewSet):
    serializer_class = UsageSummarySerializer
    permission_classes = [IsAuthenticated, CanViewBilling]

    def list(self, request):
        meter = UsageMeter()
        # Period: current month by default
        now = timezone.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        summary = meter.get_usage_summary(request.user, start, now)
        return Response(summary)


# Admin Views
class AdminPricingPlanViewSet(viewsets.ModelViewSet):
    queryset = PricingPlan.objects.all().order_by('sort_order')
    serializer_class = PricingPlanSerializer
    permission_classes = [permissions.IsAdminUser]


class AdminResourcePriceViewSet(viewsets.ModelViewSet):
    queryset = ResourcePrice.objects.all()
    serializer_class = ResourcePriceSerializer
    permission_classes = [permissions.IsAdminUser]
