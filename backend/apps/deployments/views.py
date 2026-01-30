from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Service, Deployment, EnvironmentVariable
from .serializers import (
    ServiceSerializer, DeploymentSerializer,
    DeploymentDetailSerializer, EnvironmentVariableSerializer
)
from .rate_limiting import DeploymentRateThrottle, BurstRateThrottle

class ServiceViewSet(viewsets.ModelViewSet):
    serializer_class = ServiceSerializer
    permission_classes = [IsAuthenticated]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return services owned by the current user
    # ==========================================================================
    def get_queryset(self):
        """Filter services to only those owned by the authenticated user."""
        return Service.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        # SECURITY: Always assign owner to the authenticated user
        service = serializer.save(owner=self.request.user)
        
        # Auto-inject SMSly API Keys (Vertical Integration)
        # This is the key differentiator - native SMSLY platform integration
        self._inject_smsly_keys(service, self.request.user)
    
    
    def _inject_smsly_keys(self, service, user):
        """
        Inject SMSLY API keys into the service's environment variables.
        
        This enables zero-config SMS/Voice/Video for deployed apps:
        - Apps can use process.env.SMSLY_API_KEY immediately
        - No manual API key configuration required
        - Keys are encrypted at rest
        """
        import logging
        import asyncio
        from services.smsly_client import smsly_client
        
        logger = logging.getLogger(__name__)
        
        api_key = "PLACEHOLDER_CONFIGURE_IN_DASHBOARD"
        api_secret = "PLACEHOLDER_CONFIGURE_IN_DASHBOARD"
        
        # Try to fetch real API keys from Platform API
        if hasattr(user, 'id'):
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    keys = loop.run_until_complete(
                        smsly_client.get_user_api_keys(str(user.id))
                    )
                    if keys.get('api_key'):
                        api_key = keys['api_key']
                        api_secret = keys.get('api_secret', api_secret)
                        logger.info(f"Injected real SMSLY API keys for user {user.id}")
                finally:
                    loop.close()
            except Exception as e:
                logger.warning(f"Could not fetch SMSLY keys: {e}. Using placeholder.")
        
        # Create encrypted environment variables
        EnvironmentVariable.objects.create(
            service=service,
            key="SMSLY_API_KEY",
            value=api_key,
            is_secret=True
        )
        EnvironmentVariable.objects.create(
            service=service,
            key="SMSLY_API_SECRET",
            value=api_secret,
            is_secret=True
        )
        
        # Also inject helpful SDK URLs
        EnvironmentVariable.objects.create(
            service=service,
            key="SMSLY_SMS_API_URL",
            value="https://api.smsly.cloud/v1/sms",
            is_secret=False
        )
        EnvironmentVariable.objects.create(
            service=service,
            key="SMSLY_VOICE_API_URL",
            value="https://api.smsly.cloud/v1/voice",
            is_secret=False
        )

    @action(detail=True, methods=['post'], url_path='env-vars')
    def add_env_var(self, request, pk=None):
        service = self.get_object()  # SECURITY: get_object uses filtered queryset
        serializer = EnvironmentVariableSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(service=service)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='env-vars')
    def list_env_vars(self, request, pk=None):
        service = self.get_object()  # SECURITY: get_object uses filtered queryset
        queryset = service.env_vars.all()
        
        # SECURITY: Mask secret values - never expose actual secrets via API
        data = []
        for env_var in queryset:
            item = {
                'id': env_var.id,
                'key': env_var.key,
                'is_secret': env_var.is_secret,
                'created_at': env_var.created_at,
                'updated_at': env_var.updated_at,
            }
            # Only expose value for non-secrets
            if env_var.is_secret:
                item['value'] = '********'
            else:
                item['value'] = env_var.value
            data.append(item)
        
        return Response(data)


    @action(detail=True, methods=['post'])
    def verify_domain(self, request, pk=None):
        """Verify domain ownership via DNS TXT record."""
        service = self.get_object()
        if not service.public_domain:
             return Response({"detail": "No domain configured."}, status=status.HTTP_400_BAD_REQUEST)

        # Simulate DNS check
        # In real world: import dns.resolver; dns.resolver.resolve(f"_smsly-challenge.{service.public_domain}", "TXT")
        import random
        if random.random() > 0.1: # 90% success rate for simulation
            service.domain_verified = True
            service.save()
            return Response({"status": "verified"})
        else:
            return Response({"detail": "TXT record not found. Please try again."}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='create-preview')
    def create_preview(self, request, pk=None):
        """Create a Preview Environment for a PR."""
        parent_service = self.get_object()
        pr_number = request.data.get('pr_number')
        branch = request.data.get('branch')

        if not pr_number or not branch:
            return Response({"detail": "pr_number and branch required"}, status=status.HTTP_400_BAD_REQUEST)

        # Clone service config
        preview_service = Service.objects.create(
            name=f"{parent_service.name}-pr-{pr_number}",
            repository_url=parent_service.repository_url,
            branch=branch,
            is_preview=True,
            parent_service=parent_service,
            pr_number=pr_number,
            internal_port=parent_service.internal_port,
            # Copy other fields as needed
        )

        # Trigger initial deployment
        Deployment.objects.create(
            service=preview_service,
            commit_hash="HEAD",
            status=Deployment.Status.QUEUED
        )
        # Note: In real app, trigger task here. For MVP, we assume automated via tasks.

        return Response(ServiceSerializer(preview_service).data, status=status.HTTP_201_CREATED)

class DeploymentViewSet(mixins.CreateModelMixin,
                       mixins.RetrieveModelMixin,
                       mixins.ListModelMixin,
                       viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    throttle_classes = [DeploymentRateThrottle, BurstRateThrottle]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return deployments for user's own services
    # ==========================================================================
    def get_queryset(self):
        """Filter deployments to only those belonging to the user's services."""
        return Deployment.objects.filter(service__owner=self.request.user)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return DeploymentDetailSerializer
        return DeploymentSerializer

    def create(self, request, *args, **kwargs):
        # SECURITY: Verify user owns the service before allowing deployment
        service_id = request.data.get('service')
        if service_id:
            if not Service.objects.filter(id=service_id, owner=request.user).exists():
                return Response(
                    {"detail": "Service not found or access denied."},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        response = super().create(request, *args, **kwargs)
        # Trigger Celery task
        from .tasks import run_deployment_task
        run_deployment_task.delay(response.data['id'])
        return response

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """Rollback to this deployment."""
        target_deployment = self.get_object()  # SECURITY: Uses filtered queryset
        service = target_deployment.service

        # SECURITY: Double-check ownership before rollback
        if service.owner != request.user:
            return Response(
                {"detail": "Access denied."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Create new deployment based on the target
        new_deployment = Deployment.objects.create(
            service=service,
            commit_hash=target_deployment.commit_hash,
            commit_message=f"Rollback to {target_deployment.commit_hash[:7]}: {target_deployment.commit_message}",
            status=Deployment.Status.QUEUED
        )

        # Trigger deployment logic
        from .tasks import run_deployment_task
        run_deployment_task.delay(new_deployment.id)

        serializer = DeploymentSerializer(new_deployment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
