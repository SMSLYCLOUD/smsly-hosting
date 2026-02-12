"""OAuth configuration views."""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.conf import settings


@api_view(['GET'])
@permission_classes([IsAdminUser])
def oauth_providers_status(request):
    """Get the status of OAuth providers (configured or not)."""
    try:
        github_configured = SocialApp.objects.filter(provider='github').exists()
        google_configured = SocialApp.objects.filter(provider='google').exists()
        
        return Response({
            'github': github_configured,
            'google': google_configured,
        })
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def oauth_credentials(request):
    """Get or set OAuth credentials for GitHub and Google."""
    if request.method == 'GET':
        try:
            github_app = SocialApp.objects.filter(provider='github').first()
            google_app = SocialApp.objects.filter(provider='google').first()
            
            return Response({
                'github': {
                    'configured': github_app is not None,
                    'client_id': github_app.client_id if github_app else '',
                    # Never return the secret
                } if github_app else {'configured': False},
                'google': {
                    'configured': google_app is not None,
                    'client_id': google_app.client_id if google_app else '',
                    # Never return the secret
                } if google_app else {'configured': False},
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
                            'client_id': github_data['client_id'],
                            'secret': github_data['client_secret'],
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
                            'client_id': google_data['client_id'],
                            'secret': google_data['client_secret'],
                        }
                    )
                    google_app.sites.add(site)
            
            return Response({'success': True})
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
