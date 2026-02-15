"""Views module."""
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import CloudProvider, CloudResource, Secret
from .serializers import CloudProviderSerializer, CloudProviderCreateSerializer, CloudResourceSerializer, SecretSerializer
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine
from apps.intelligence.cost import CostAdvisor
from apps.intelligence.providers import get_provider, get_available_providers, ask_with_fallback, SYSTEM_PROMPT


class CloudProviderViewSet(viewsets.ModelViewSet):
    queryset = CloudProvider.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # Provider credential mutations are admin-only.
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

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

        response_text, provider_name = ask_with_fallback(message, system_prompt=SYSTEM_PROMPT)
        return Response({
            'response': response_text,
            'provider': provider_name,
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
        ai_prompt = (
            f"Analyze these deployment logs and provide a diagnosis with fix suggestions.\n"
            f"Deployment ID: {deployment_id}\n\n"
            f"Logs:\n```\n{logs[:3000]}\n```\n\n"
            f"Known issues found by pattern matching: {issues if issues else 'None'}\n\n"
            f"Provide: 1) Root cause, 2) Fix steps, 3) Prevention tips."
        )
        ai_diagnosis, provider_name = ask_with_fallback(ai_prompt, system_prompt=SYSTEM_PROMPT)

        return Response({
            'pattern_issues': issues,
            'ai_diagnosis': ai_diagnosis,
            'provider': provider_name,
        })

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser])
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

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def update_ai_config(self, request):
        """
        Update AI provider configuration.
        POST /api/v1/cloud/intelligence/update_ai_config/
        Body: { "provider": "grok", "api_key": "xai-..." }
        """
        import os
        from apps.intelligence.models import AIProviderSettings

        provider_name = request.data.get('provider', '').strip().lower()
        api_key = request.data.get('api_key', '').strip()

        valid_providers = ['openai', 'grok', 'gemini', 'mock']
        if provider_name not in valid_providers:
            return Response(
                {'error': f'Invalid provider. Choose from: {valid_providers}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Map provider to env var name
        key_map = {
            'openai': 'OPENAI_API_KEY',
            'grok': 'GROK_API_KEY',
            'gemini': 'GEMINI_API_KEY',
        }

        # Set provider in environment (immediate effect)
        os.environ['AI_PROVIDER'] = provider_name

        # Set API key if provided
        if api_key and provider_name in key_map:
            env_var = key_map[provider_name]
            os.environ[env_var] = api_key

        # Persist to DB so config survives container restarts.
        cfg = AIProviderSettings.get_solo()
        cfg.active_provider = provider_name
        field_map = {
            'openai': 'openai_api_key',
            'grok': 'grok_api_key',
            'gemini': 'gemini_api_key',
        }
        if api_key and provider_name in field_map:
            setattr(cfg, field_map[provider_name], api_key)
        cfg.save()

        return Response({
            'status': 'saved',
            'provider': provider_name,
            'key_set': bool(api_key),
        })


class EcosystemViewSet(viewsets.ViewSet):
    """Zero-config AI ecosystem deployment endpoints."""
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'])
    def scan(self, request):
        """
        Scan all GitHub repos and return an AI-generated deploy plan.
        POST /api/v1/cloud/ecosystem/scan/
        """
        from apps.deployments.tasks_ecosystem import ecosystem_scan_task
        result = ecosystem_scan_task.delay(str(request.user.id))
        return Response({
            'task_id': result.id,
            'status': 'scanning',
            'message': 'Scanning your GitHub repositories...',
        })

    @action(detail=False, methods=['post'])
    def deploy(self, request):
        """
        Deploy all services from a scan plan.
        POST /api/v1/cloud/ecosystem/deploy/
        Body: { "plan": { ... } }
        """
        plan = request.data.get('plan')
        if not plan:
            return Response(
                {'error': 'Deploy plan is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        from apps.deployments.tasks_ecosystem import ecosystem_deploy_task
        result = ecosystem_deploy_task.delay(str(request.user.id), plan)
        return Response({
            'task_id': result.id,
            'status': 'deploying',
            'message': 'Deploying your ecosystem...',
        })

    @action(detail=False, methods=['get'])
    def task_status(self, request):
        """
        Check the status of a scan or deploy task.
        GET /api/v1/cloud/ecosystem/task_status/?task_id=xxx
        """
        from celery.result import AsyncResult
        task_id = request.query_params.get('task_id')
        if not task_id:
            return Response(
                {'error': 'task_id is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = AsyncResult(task_id)
        response = {
            'task_id': task_id,
            'status': result.status,  # PENDING, STARTED, SUCCESS, FAILURE
        }
        if result.ready():
            if result.successful():
                response['result'] = result.result
            else:
                response['error'] = str(result.result)
        return Response(response)

    @action(detail=False, methods=['post'])
    def analyze(self, request):
        """
        Analyze a single repository and return deploy configuration.
        POST /api/v1/cloud/ecosystem/analyze/
        Body: { "repo_url": "https://github.com/user/repo" }
        Returns stack, languages, port, build strategy, addons, suggested env vars.
        """
        import tempfile, os
        from services.ecosystem import heuristic_analysis
        from apps.deployments.services.git import GitManager

        repo_url = request.data.get('repo_url')
        if not repo_url:
            return Response(
                {'error': 'repo_url is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get GitHub OAuth token for authenticated clones
        token = None
        try:
            from apps.deployments.tasks import _get_github_oauth_token_for_user
            token = _get_github_oauth_token_for_user(request.user)
        except Exception:
            pass  # No token = public repos only

        # Extract repo name
        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')

        try:
            with tempfile.TemporaryDirectory() as tmp:
                clone_dir = GitManager.clone_repo(
                    repo_url=repo_url,
                    branch=request.data.get('branch', 'main'),
                    destination=tmp,
                    token=token,
                )

                # List all files relative to clone root
                files = []
                for root, dirs, filenames in os.walk(clone_dir):
                    # Skip .git
                    dirs[:] = [d for d in dirs if d != '.git']
                    for f in filenames:
                        rel = os.path.relpath(
                            os.path.join(root, f), clone_dir
                        ).replace('\\', '/')
                        files.append(rel)

                analysis = heuristic_analysis(files)

            return Response({
                'repo': repo_url,
                'name': repo_name,
                **analysis,
            })

        except Exception as e:
            # Fallback: return simulated defaults so the UI flow isn't blocked
            import logging
            logging.getLogger(__name__).warning(
                "Analyze clone failed for %s, returning simulated result: %s", repo_url, e
            )
            return Response({
                'repo': repo_url,
                'name': repo_name,
                'simulated': True,
                'stack': 'node',
                'languages': ['javascript'],
                'framework': None,
                'port': 3000,
                'build_command': 'npm run build',
                'start_command': 'npm start',
                'build_strategy': 'buildpack',
                'addons': [],
                'env_vars': {},
                'message': 'Analysis could not clone the repo. Defaults provided — configure manually.',
            })

