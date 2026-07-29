from django.test import override_settings

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-deployments",
    }
}

FAST_THROTTLE_RATES = {
    "anon": "200/hour",
    "user": "5000/hour",
    "caddy_ask": "1000/min",
}

REST_FRAMEWORK_LOOSE = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.deployments.models.api_token.APITokenAuthentication",
        "apps.deployments.models.api_token.RemoteSyncHMACAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "apps.core.auth.CsrfExemptSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": FAST_THROTTLE_RATES,
}
