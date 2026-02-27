"""Views module."""
from rest_framework import serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine
from apps.intelligence.cost import CostAdvisor
from apps.intelligence.providers import get_available_providers, ask_with_fallback, SYSTEM_PROMPT
from .models import CloudProvider, CloudResource
from .serializers import CloudProviderSerializer, CloudProviderCreateSerializer, CloudResourceSerializer


class CloudProviderViewSet(viewsets.ModelViewSet):
    # M-3 fix: non-admin users only see active providers (no credential details)
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CloudProviderSerializer

    def get_queryset(self):
        # Only return active providers for regular users
        if self.request.user.is_staff:
            return CloudProvider.objects.all()
        return CloudProvider.objects.filter(is_active=True)

    def get_serializer_class(self):
        if self.action == 'create':
            return CloudProviderCreateSerializer
        return CloudProviderSerializer

    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        # Trigger async sync task
        # This is a placeholder for actual cloud sync logic
        return Response({'status': 'Sync started'})

    @action(detail=False, methods=['get'])
    def available_regions(self, request):
        # Return mocked regions for now
        return Response([
            {'id': 'us-east-1', 'name': 'US East (N. Virginia)'},
            {'id': 'eu-central-1', 'name': 'Europe (Frankfurt)'},
        ])


class CloudResourceViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = CloudResourceSerializer
    queryset = CloudResource.objects.all()


class IntelligencePayloadSerializer(serializers.Serializer):
    data = serializers.JSONField(required=False)


class IntelligenceViewSet(viewsets.GenericViewSet):
    """
    AI-powered cloud optimization and debugging.
    """
    serializer_class = IntelligencePayloadSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'])
    def analyze_logs(self, request):
        """Analyze logs for errors and anomalies."""
        logs = request.data.get('logs', [])
        # service_id = request.data.get('service_id')

        analyzer = LogAnalyzer()
        # analyzer.analyze_logs expects a string, not a list
        if isinstance(logs, list):
            logs = "\n".join(logs)

        analysis = analyzer.analyze_logs(logs)

        return Response(analysis)

    @action(detail=False, methods=['post'])
    def suggest_remediation(self, request):
        """Suggest fixes for a specific error."""
        error_msg = request.data.get('error')
        # context = request.data.get('context', {})

        remediator = RemediationEngine()
        # Only pass issue_type (error_msg)
        plan = remediator.suggest_fix(error_msg)

        return Response(plan)

    @action(detail=False, methods=['get'])
    def optimize_cost(self, request):
        """Analyze current usage and suggest cost optimizations."""
        advisor = CostAdvisor()
        # Check if generate_report exists before calling
        if hasattr(advisor, 'generate_report'):
            report = advisor.generate_report(request.user)
        else:
            report = {"message": "Cost optimization module not fully active."}

        return Response(report)

    @action(detail=False, methods=['post'])
    def chat(self, request):
        """Interactive AI assistant for cloud ops."""
        message = (request.data.get('message') or '').strip()
        if not message:
            return Response(
                {'error': 'message is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # history = request.data.get('history', [])

        # Add system context
        context = "User is asking about cloud infrastructure."

        response, provider_name = ask_with_fallback(
            prompt=f"{context}\nUser: {message}",
            system_prompt=SYSTEM_PROMPT
        )

        return Response({'response': response, 'provider': provider_name})

    @action(detail=False, methods=['get'])
    def providers(self, request):
        """List available AI providers and their status."""
        return Response(get_available_providers())

    @action(detail=False, methods=['post'])
    def troubleshoot(self, request):
        """
        Troubleshoot a specific deployment or service error.
        M-5: Deep diagnostic tool.
        """
        # deployment_id = request.data.get('deployment_id')
        error_trace = request.data.get('error_trace')

        # M-5: Enhanced Troubleshooting logic
        # 1. Fetch deployment logs if ID provided
        # 2. Analyze error trace
        # 3. Check resource utilization
        # 4. Query AI for root cause analysis

        analysis_result = {
            "root_cause": "Unknown",
            "confidence": 0.0,
            "suggested_actions": [],
            "related_logs": []
        }

        # Mock logic for now
        if error_trace:
            analysis_result["root_cause"] = "Configuration Error detected in environment variables."
            analysis_result["confidence"] = 0.85
            analysis_result["suggested_actions"] = ["Verify DATABASE_URL format", "Check for missing API keys"]

        return Response(analysis_result)

    @action(detail=False, methods=['post'])
    def generate_iac(self, request):
        """
        Generate Infrastructure as Code (Terraform/Pulumi) from natural language description.
        """
        description = request.data.get('description')
        target_cloud = request.data.get('cloud', 'aws')

        # Placeholder for IaC generation
        iac_code = f"# Terraform configuration for {description}\nprovider \"{target_cloud}\" {{}}"

        return Response({'code': iac_code, 'language': 'hcl'})

    @action(detail=False, methods=['post'])
    def ecosystem_scan(self, request):
        """
        Scan all accessible GitHub repositories and generate a zero-click deploy plan.
        """
        from apps.deployments.tasks_ecosystem import ecosystem_scan_task
        # Keep a stable call signature for API/tests while task internals may evolve.
        task = ecosystem_scan_task.delay(str(request.user.id), 30)

        return Response({'task_id': task.id, 'status': 'scanning'})

    @action(detail=False, methods=['post'])
    def ecosystem_deploy(self, request):
        """
        Deploy a previously generated ecosystem plan.
        """
        plan = request.data.get('plan')
        if not isinstance(plan, dict):
            return Response(
                {'error': 'plan (object) is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.tasks_ecosystem import ecosystem_deploy_task
        task = ecosystem_deploy_task.delay(str(request.user.id), plan)

        return Response({'task_id': task.id, 'status': 'deploying'})

    @action(detail=False, methods=['get'])
    def task_status(self, request):
        """
        Check status of a long-running background task (Celery).
        """
        task_id = request.query_params.get('task_id')
        if not task_id:
            return Response({'error': 'task_id required'}, status=status.HTTP_400_BAD_REQUEST)

        import json
        from celery.result import AsyncResult
        result = AsyncResult(task_id)

        payload = None
        if result.ready():
            try:
                payload = result.result
            except Exception as exc:  # pylint: disable=broad-exception-caught
                payload = {
                    'error': str(exc),
                    'exception_type': exc.__class__.__name__,
                }

            if isinstance(payload, Exception):
                payload = {
                    'error': str(payload),
                    'exception_type': payload.__class__.__name__,
                }
            else:
                try:
                    json.dumps(payload)
                except TypeError:
                    payload = str(payload)

        response_data = {
            'task_id': task_id,
            'status': result.status,
            'result': payload,
        }
        return Response(response_data)

    @action(detail=False, methods=['post'])
    def analyze_repo(self, request):
        """
        Analyzes a repository to determine stack, build method, and required ports/env vars.
        Returns stack, languages, port, build strategy, addons, suggested env vars.
        """
        import tempfile
        import os
        from services.ecosystem import heuristic_analysis
        from apps.deployments.services.git_manager import GitManager

        repo_url = request.data.get('repo_url')
        if not repo_url:
            return Response(
                {'error': 'repo_url is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # M-5 fix: validate repo URL against allow-listed Git hosts
        import re as _re
        if not _re.match(
            r'^https://(github\.com|gitlab\.com|bitbucket\.org)/',
            repo_url,
        ):
            return Response(
                {'error': 'Only GitHub, GitLab, and Bitbucket URLs are allowed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )



        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # We need a token if it's a private repo
                # This is where we need the fix: use the new utility from utils.py
                # instead of importing from tasks.py which causes circular import issues.
                from apps.deployments.utils import get_github_oauth_token_for_user
                token = get_github_oauth_token_for_user(request.user)

                # Clone using static method (GitManager has no __init__)
                try:
                    project_path = GitManager.clone_repo(
                        repo_url, destination=temp_dir, token=token
                    )
                except Exception as e:
                    return Response(
                        {'error': f'Failed to clone repository: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Analyze
                # Get list of files relative to project path
                project_files = []
                for root, _, filenames in os.walk(project_path):
                    for f in filenames:
                        rel_path = os.path.relpath(os.path.join(root, f), project_path)
                        project_files.append(rel_path)

                analysis_results = heuristic_analysis(project_files, clone_dir=project_path)

                return Response(analysis_results)

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Repo analysis failed: %s", e, exc_info=True)
            return Response(
                {'error': f'Analysis failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
