from allauth.socialaccount.models import SocialApp
from django.http import JsonResponse


class SocialAppNotFoundMiddleware:
    """Convert allauth's SocialApp.DoesNotExist to a JSON 404.

    When a user hits /accounts/<provider>/login/ (or the /api/v1/accounts/
    alias) and no SocialApp is configured for that provider, allauth's
    OAuth2LoginView raises SocialApp.DoesNotExist, which Django turns into
    a 500. That is log noise and a misleading status code. Return 404 with a
    clear message instead.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, SocialApp.DoesNotExist):
            if request.path.startswith('/api/'):
                return JsonResponse(
                    {'error': 'Social login provider not configured'},
                    status=404,
                )
            from django.http import Http404
            raise Http404('Social login provider not configured')
        return None
