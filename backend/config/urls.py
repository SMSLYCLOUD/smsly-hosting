from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),  # OAuth social login
    path('api/v1/auth/', include('dj_rest_auth.urls')),  # REST auth endpoints
    path('api/v1/auth/registration/', include('dj_rest_auth.registration.urls')),  # Registration
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/', include('apps.teams.urls')),
    path('api/health/', health_check),
]
