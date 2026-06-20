from django.contrib import admin

from .models import (
    BillingAccount,
    BillingPayment,
    Invoice,
    PricingPlan,
    ResourcePrice,
    UsageRecord,
    UserSubscription,
)


@admin.register(PricingPlan)
class PricingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_monthly_usd', 'is_active', 'sort_order')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(ResourcePrice)
class ResourcePriceAdmin(admin.ModelAdmin):
    list_display = ('resource_type', 'price_per_unit_monthly', 'unit_label', 'is_active')

@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'billing_cycle', 'current_period_end')
    list_filter = ('status', 'billing_cycle')

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'total', 'status', 'period_end')
    list_filter = ('status',)

admin.site.register(BillingAccount)
admin.site.register(BillingPayment)
admin.site.register(UsageRecord)
