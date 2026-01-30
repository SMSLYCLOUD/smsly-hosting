from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),  # OAuth social login
    path('api/v1/cloud/', include('apps.cloud.urls')),
    # Wire up dj-rest-auth for Login/Register APIs
    path('api/v1/auth/', include('dj_rest_auth.urls')),
    path('api/v1/auth/registration/', include('dj_rest_auth.registration.urls')),
    path('api/v1/', include('apps.deployments.urls')),
]
