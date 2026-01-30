from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import CloudProvider, CloudResource, Secret
from .serializers import CloudProviderSerializer, CloudProviderCreateSerializer, CloudResourceSerializer, SecretSerializer
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine
from apps.intelligence.cost import CostAdvisor

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
        return Response({'message': 'No suggestion found'}, status=status.HTTP_404_NOT_FOUND)

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
