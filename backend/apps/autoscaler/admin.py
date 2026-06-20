from django.contrib import admin

from .models import AutoscalerConfig


@admin.register(AutoscalerConfig)
class AutoscalerConfigAdmin(admin.ModelAdmin):
    list_display = ('id', 'updated_at')
    readonly_fields = ('updated_at',)
