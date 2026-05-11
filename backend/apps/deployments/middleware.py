from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class DynamicAllowedHostsMiddleware:
    """
    Dynamically patches ALLOWED_HOSTS if an incoming request's host matches 
    the domain configured in PlatformConfig. This completely solves 
    multi-process stale state where one Gunicorn worker updates the DB 
    but other workers haven't reloaded ALLOWED_HOSTS yet.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.META.get('HTTP_HOST', '').split(':')[0]
        if host and host not in settings.ALLOWED_HOSTS:
            try:
                from apps.deployments.patching import is_valid_host, patch_runtime_settings
                if is_valid_host(host):
                    settings.ALLOWED_HOSTS.append(host)
                    logger.info("DynamicAllowedHostsMiddleware: instantly whitelisted valid domain %s", host)
                else:
                    # Still run the standard sync for origin patching just in case
                    patch_runtime_settings()
            except Exception as e:
                logger.warning("Dynamic host patching failed: %s", e)
                
        return self.get_response(request)
