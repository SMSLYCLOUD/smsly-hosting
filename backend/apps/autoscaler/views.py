import os
import requests
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

AUTOSCALER_URL = getattr(settings, 'AUTOSCALER_API_URL', 'http://localhost:9876')
AUTOSCALER_TOKEN = os.environ.get('AUTOSCALER_API_TOKEN', '')


def _autoscaler_headers():
    """Build headers for autoscaler API requests (adds auth if token set)."""
    headers = {}
    if AUTOSCALER_TOKEN:
        headers['Authorization'] = f'Bearer {AUTOSCALER_TOKEN}'
    return headers


@api_view(['GET'])
@permission_classes([IsAdminUser])
def autoscaler_status(request):
    """Proxy to autoscaler /api/status"""
    try:
        r = requests.get(f'{AUTOSCALER_URL}/api/status', timeout=5)
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e), 'autoscaler_reachable': False}, status=503)

@api_view(['GET'])
@permission_classes([IsAdminUser])
def autoscaler_history(request):
    """Proxy to autoscaler /api/history"""
    minutes = request.query_params.get('minutes', '60')
    try:
        r = requests.get(f'{AUTOSCALER_URL}/api/history', params={'minutes': minutes}, timeout=5)
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e)}, status=503)

@api_view(['POST'])
@permission_classes([IsAdminUser])
def autoscaler_config(request):
    """Proxy config update to autoscaler"""
    try:
        r = requests.post(
            f'{AUTOSCALER_URL}/api/config',
            json=request.data,
            headers=_autoscaler_headers(),
            timeout=5
        )
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e)}, status=503)

@api_view(['POST'])
@permission_classes([IsAdminUser])
def autoscaler_trigger(request):
    """Trigger an immediate autoscaler check"""
    try:
        r = requests.post(
            f'{AUTOSCALER_URL}/api/trigger',
            headers=_autoscaler_headers(),
            timeout=15
        )
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e)}, status=503)
