"""OAuth configuration views."""
from collections import OrderedDict

from allauth.socialaccount import providers as allauth_providers
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

# SECURITY (Issue 23): the oauth_credentials POST handler writes
# credentials to SocialApp and the dashboard expects the next
# OAuth flow to pick up the new values immediately. allauth
# memoises its provider registry in-process, so a stale
# SocialApp would still be served until the worker restarts.
# The post_save / post_delete receivers below bust the relevant
# cache keys AND wipe allauth's in-process provider registry on
# any SocialApp change, so the next call sees the fresh values.
_OAUTH_CACHE_PREFIX = "social_app"


@receiver(post_save, sender=SocialApp)
@receiver(post_delete, sender=SocialApp)
def _invalidate_social_app_cache(_sender, instance, **kwargs):
    cache.delete(f"{_OAUTH_CACHE_PREFIX}:{instance.provider}:{instance.id}")
    # allauth memoises providers in an OrderedDict. Clearing it
    # and resetting ``loaded`` forces the next ``get_class`` call
    # to re-import the provider module — and the provider module
    # re-reads SOCIALACCOUNT_PROVIDERS (and any consumer that
    # re-queries SocialApp) picks up the new values.
    try:
        allauth_providers.registry.provider_map = OrderedDict()
        allauth_providers.registry.loaded = False
    except Exception:
        pass


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([IsAdminUser])
def oauth_providers_status(request):
    """Get the status of OAuth providers (configured or not)."""
    try:
        github_configured = SocialApp.objects.filter(provider='github').exists()
        google_configured = SocialApp.objects.filter(provider='google').exists()
        gitlab_configured = SocialApp.objects.filter(provider='gitlab').exists()
        bitbucket_configured = SocialApp.objects.filter(provider='bitbucket_oauth2').exists()

        return Response({
            'github': github_configured,
            'google': google_configured,
            'gitlab': gitlab_configured,
            'bitbucket': bitbucket_configured,
        })
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def oauth_credentials(request):
    """Get or set OAuth credentials for GitHub, Google, GitLab, and Bitbucket."""
    if request.method == 'GET':
        try:
            github_app = SocialApp.objects.filter(provider='github').first()
            google_app = SocialApp.objects.filter(provider='google').first()
            gitlab_app = SocialApp.objects.filter(provider='gitlab').first()
            bitbucket_app = SocialApp.objects.filter(provider='bitbucket_oauth2').first()

            return Response({
                'github': {
                    'configured': github_app is not None,
                    'client_id': github_app.client_id if github_app else '',
                } if github_app else {'configured': False},
                'google': {
                    'configured': google_app is not None,
                    'client_id': google_app.client_id if google_app else '',
                } if google_app else {'configured': False},
                'gitlab': {
                    'configured': gitlab_app is not None,
                    'client_id': gitlab_app.client_id if gitlab_app else '',
                } if gitlab_app else {'configured': False},
                'bitbucket': {
                    'configured': bitbucket_app is not None,
                    'client_id': bitbucket_app.client_id if bitbucket_app else '',
                } if bitbucket_app else {'configured': False},
            })
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    elif request.method == 'POST':
        try:
            data = request.data
            site = Site.objects.get(id=settings.SITE_ID)

            # Update GitHub
            if 'github' in data:
                github_data = data['github']
                if github_data.get('client_id') and github_data.get('client_secret'):
                    github_app, _ = SocialApp.objects.update_or_create(
                        provider='github',
                        defaults={
                            'name': 'GitHub',
                            'client_id': github_data['client_id'].strip(),
                            'secret': github_data['client_secret'].strip(),
                        }
                    )
                    github_app.sites.add(site)

            # Update Google
            if 'google' in data:
                google_data = data['google']
                if google_data.get('client_id') and google_data.get('client_secret'):
                    google_app, _ = SocialApp.objects.update_or_create(
                        provider='google',
                        defaults={
                            'name': 'Google',
                            'client_id': google_data['client_id'].strip(),
                            'secret': google_data['client_secret'].strip(),
                        }
                    )
                    google_app.sites.add(site)

            # Update GitLab
            if 'gitlab' in data:
                gitlab_data = data['gitlab']
                if gitlab_data.get('client_id') and gitlab_data.get('client_secret'):
                    gitlab_app, _ = SocialApp.objects.update_or_create(
                        provider='gitlab',
                        defaults={
                            'name': 'GitLab',
                            'client_id': gitlab_data['client_id'].strip(),
                            'secret': gitlab_data['client_secret'].strip(),
                        }
                    )
                    gitlab_app.sites.add(site)

            # Update Bitbucket
            if 'bitbucket' in data:
                bitbucket_data = data['bitbucket']
                if bitbucket_data.get('client_id') and bitbucket_data.get('client_secret'):
                    bitbucket_app, _ = SocialApp.objects.update_or_create(
                        provider='bitbucket_oauth2',
                        defaults={
                            'name': 'Bitbucket',
                            'client_id': bitbucket_data['client_id'].strip(),
                            'secret': bitbucket_data['client_secret'].strip(),
                        }
                    )
                    bitbucket_app.sites.add(site)

            return Response({'success': True})
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
