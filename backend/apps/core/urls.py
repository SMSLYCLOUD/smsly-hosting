"""Core app URL patterns."""
from django.urls import path
from apps.core.views import ContactView

urlpatterns = [
    path('contact/', ContactView.as_view(), name='contact'),
]
