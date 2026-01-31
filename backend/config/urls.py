from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),  # OAuth social login
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/', include('apps.deployments.urls')),
]
