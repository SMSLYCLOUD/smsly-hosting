from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .webhooks.github import GitHubWebhookHandler

class GitHubWebhookView(APIView):
    authentication_classes = [] # Webhooks use signature auth
    permission_classes = []

    def post(self, request):
        handler = GitHubWebhookHandler()

        # 1. Verify Signature
        if settings.GITHUB_WEBHOOK_SECRET and not handler.verify_signature(request):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN)

        # 2. Parse Event
        event_type = request.headers.get('X-GitHub-Event')
        if not event_type:
            return Response({'error': 'Missing event type'}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Handle Logic
        try:
            triggered = handler.handle_event(event_type, request.data)
            return Response({'message': 'Webhook processed', 'triggered': triggered})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
