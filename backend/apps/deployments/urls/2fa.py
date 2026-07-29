from django.urls import path

from ..views import _2fa as views_2fa

urlpatterns = [
    path('status/', views_2fa.two_factor_status, name='2fa-status'),
    path('enable/', views_2fa.two_factor_enable, name='2fa-enable'),
    path('confirm/', views_2fa.two_factor_confirm, name='2fa-confirm'),
    path('disable/', views_2fa.two_factor_disable, name='2fa-disable'),
    path('login/', views_2fa.two_factor_login, name='2fa-login'),
    path('backup-codes/', views_2fa.two_factor_backup_codes, name='2fa-backup-codes'),
]
