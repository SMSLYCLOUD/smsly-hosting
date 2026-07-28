"""remote views."""
import logging

logger = logging.getLogger(__name__)



from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from ..models import Deployment, Service
from ..serializers import DeploymentSerializer, DeploymentTriggerSerializer
from ..tasks.deploy.helpers import enqueue_smart_deploy_task
from ._helpers import ZeroTrustHMACAuthentication
class RemoteTriggerView(GenericAPIView):
    """
    Direct endpoint for node-to-node deployment triggers.
    Authenticated via ZeroTrustHMACAuthentication.
    """
    authentication_classes = [ZeroTrustHMACAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeploymentTriggerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        service_id = serializer.validated_data['service_id']
        provider_id = serializer.validated_data['provider_id']
        skip_review = serializer.validated_data.get('skip_review', False)
        ref = serializer.validated_data.get('commit_hash', 'HEAD')
        source_node = request.data.get('source_node', 'remote-controller')

        try:
            service = Service.objects.get(id=service_id)
            # Determine provider (or use the one passed in if it belongs to this node)
            from apps.cloud.models import CloudProvider
            provider = CloudProvider.objects.filter(id=provider_id).first()
            if not provider:
                # Fallback to resolving local provider
                from ._helpers import _resolve_provider_for_service
                provider = _resolve_provider_for_service(service)

            if not provider:
                return Response({"error": "No valid cloud provider found on this node"}, status=400)

            # Create deployment
            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=ref if ref != 'HEAD' else 'latest',
                commit_message=f"Remote Trigger: {ref} (via {source_node})",
                source_node=source_node
            )

            # Enqueue task
            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=skip_review
            )

            return Response(DeploymentSerializer(deployment).data, status=status.HTTP_201_CREATED)

        except Service.DoesNotExist:
            return Response({"error": "Service not found on this node"}, status=404)
        except Exception as e:
            logger.exception("Remote trigger failed")
            return Response({"error": str(e)}, status=500)



