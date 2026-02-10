"""Views module."""
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import CloudProvider, CloudResource, Secret
from .serializers import CloudProviderSerializer, CloudProviderCreateSerializer, CloudResourceSerializer, SecretSerializer
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine
from apps.intelligence.cost import CostAdvisor
from apps.intelligence.providers import get_provider, get_available_providers, SYSTEM_PROMPT


class CloudProviderViewSet(viewsets.ModelViewSet):
    queryset = CloudProvider.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return CloudProviderCreateSerializer
        return CloudProviderSerializer

    def perform_create(self, serializer):
        # In a real app, validate credentials here before saving
        serializer.save()


class CloudResourceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CloudResource.objects.all()
    serializer_class = CloudResourceSerializer
    permission_classes = [permissions.IsAuthenticated]


class IntelligenceViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'])
    def analyze_logs(self, request):
        """
        Analyze logs for failure patterns.
        POST /api/v1/intelligence/analyze_logs/
        Body: { "logs": "..." }
        """
        logs = request.data.get('logs', '')
        analyzer = LogAnalyzer()
        issues = analyzer.analyze_logs(logs)
        return Response({'issues': issues})

    @action(detail=False, methods=['post'])
    def remediate(self, request):
        """
        Get remediation suggestion.
        POST /api/v1/intelligence/remediate/
        Body: { "issue_type": "OOM_KILLED" }
        """
        issue_type = request.data.get('issue_type')
        engine = RemediationEngine()
        suggestion = engine.suggest_fix(issue_type)
        if suggestion:
            return Response(suggestion)
        return Response({'message': 'No suggestion found'},
                        status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'])
    def estimate_cost(self, request):
        """
        Compare costs across providers.
        POST /api/v1/intelligence/estimate_cost/
        Body: { "cpu": 2, "memory_gb": 4 }
        """
        cpu = request.data.get('cpu', 1)
        memory = request.data.get('memory_gb', 1)
        advisor = CostAdvisor()
        estimates = advisor.estimate_monthly_cost(float(cpu), float(memory))
        return Response({'estimates': estimates})

    # ---- AI Chat Endpoints ----

    @action(detail=False, methods=['post'])
    def ask(self, request):
        """
        General AI assistant chat.
        POST /api/v1/cloud/intelligence/ask/
        Body: { "message": "How do I fix OOM errors?" }
        """
        message = request.data.get('message', '').strip()
        if not message:
            return Response(
                {'error': 'Message is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(message) > 2000:
            return Response(
                {'error': 'Message too long (max 2000 chars).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        provider = get_provider()
        response_text = provider.ask(message, system_prompt=SYSTEM_PROMPT)
        return Response({
            'response': response_text,
            'provider': provider.name(),
        })

    @action(detail=False, methods=['post'])
    def diagnose(self, request):
        """
        AI-powered log diagnosis.
        POST /api/v1/cloud/intelligence/diagnose/
        Body: { "logs": "...", "deployment_id": "optional" }
        """
        logs = request.data.get('logs', '').strip()
        deployment_id = request.data.get('deployment_id', 'unknown')
        if not logs:
            return Response(
                {'error': 'Logs are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # First try regex patterns
        analyzer = LogAnalyzer()
        issues = analyzer.analyze_logs(logs)

        # Then ask AI for deeper analysis
        provider = get_provider()
        ai_prompt = (
            f"Analyze these deployment logs and provide a diagnosis with fix suggestions.\n"
            f"Deployment ID: {deployment_id}\n\n"
            f"Logs:\n```\n{logs[:3000]}\n```\n\n"
            f"Known issues found by pattern matching: {issues if issues else 'None'}\n\n"
            f"Provide: 1) Root cause, 2) Fix steps, 3) Prevention tips."
        )
        ai_diagnosis = provider.ask(ai_prompt, system_prompt=SYSTEM_PROMPT)

        return Response({
            'pattern_issues': issues,
            'ai_diagnosis': ai_diagnosis,
            'provider': provider.name(),
        })

    @action(detail=False, methods=['get'])
    def ai_config(self, request):
        """
        Get current AI provider configuration.
        GET /api/v1/cloud/intelligence/ai_config/
        """
        provider = get_provider()
        providers_list = get_available_providers()
        return Response({
            'active_provider': provider.name(),
            'providers': providers_list,
        })

