"""Views module."""
import re
from decimal import Decimal
import logging

from rest_framework import serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine
from apps.intelligence.cost import CostAdvisor
from apps.intelligence.providers import get_available_providers, ask_with_fallback, SYSTEM_PROMPT
from .models import CloudProvider, CloudResource
from .serializers import CloudProviderSerializer, CloudProviderCreateSerializer, CloudResourceSerializer

logger = logging.getLogger(__name__)


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
        """
        Validate provider connectivity and refresh provider activation state.
        """
        provider = self.get_object()

        if not request.user.is_staff:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from apps.cloud.services.compute import ComputeService
            compute_service = ComputeService(provider)
            authenticated = bool(compute_service.adapter.authenticate())
        except NotImplementedError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:  # noqa: BLE001
            return Response(
                {'error': f'Sync failed: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider.is_active = authenticated
        provider.save(update_fields=['is_active', 'updated_at'])

        resource_count = CloudResource.objects.filter(provider=provider).count()
        return Response({
            'status': 'synced' if authenticated else 'auth_failed',
            'provider_id': str(provider.id),
            'provider_type': provider.provider_type,
            'is_active': provider.is_active,
            'resource_count': resource_count,
        })

    @action(detail=True, methods=['post'])
    def validate_credentials(self, request, pk=None):
        provider = self.get_object()

        # Only admins can trigger validation
        if not request.user.is_staff:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from apps.cloud.services.compute import ComputeService
            compute_service = ComputeService(provider)
            adapter = compute_service.adapter
            is_valid = adapter.authenticate()
            return Response({
                'status': 'success' if is_valid else 'failed',
                'message': 'Credentials are valid' if is_valid else 'Authentication failed'
            })
        except Exception as e:
            # Mask the exact error if it contains sensitive keys
            error_msg = str(e)
            if provider.api_key and provider.api_key in error_msg:
                error_msg = error_msg.replace(provider.api_key, '***')
            if provider.api_secret and provider.api_secret in error_msg:
                error_msg = error_msg.replace(provider.api_secret, '***')

            return Response({
                'status': 'error',
                'message': f'Validation failed: {error_msg}'
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def available_regions(self, request):
        """
        Return known deployable regions by provider.

        Query params:
          - provider_type (optional): AWS|GCP|AZURE|LOCAL|RAILWAY|VERCEL
        """
        catalog = {
            CloudProvider.ProviderType.AWS: [
                {'id': 'af-south-1', 'name': 'Cape Town'},
                {'id': 'eu-west-2', 'name': 'London'},
                {'id': 'eu-central-1', 'name': 'Frankfurt'},
                {'id': 'us-east-1', 'name': 'N. Virginia'},
            ],
            CloudProvider.ProviderType.GCP: [
                {'id': 'europe-west1', 'name': 'Belgium'},
                {'id': 'europe-west3', 'name': 'Frankfurt'},
                {'id': 'us-central1', 'name': 'Iowa'},
                {'id': 'us-east1', 'name': 'South Carolina'},
            ],
            CloudProvider.ProviderType.AZURE: [
                {'id': 'westeurope', 'name': 'West Europe'},
                {'id': 'uksouth', 'name': 'UK South'},
                {'id': 'eastus', 'name': 'East US'},
                {'id': 'southafricanorth', 'name': 'South Africa North'},
            ],
            CloudProvider.ProviderType.LOCAL: [
                {'id': 'local', 'name': 'Local Cluster'},
            ],
            CloudProvider.ProviderType.RAILWAY: [
                {'id': 'us-west', 'name': 'US West'},
                {'id': 'eu-west', 'name': 'EU West'},
            ],
            CloudProvider.ProviderType.VERCEL: [
                {'id': 'iad1', 'name': 'Washington, D.C.'},
                {'id': 'cdg1', 'name': 'Paris'},
                {'id': 'sin1', 'name': 'Singapore'},
            ],
        }

        provider_type = (request.query_params.get('provider_type') or '').strip().upper()
        if provider_type:
            if provider_type not in catalog:
                return Response(
                    {'error': f'Unknown provider_type: {provider_type}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = catalog[provider_type]
            return Response([{'provider': provider_type, **row} for row in rows])

        rows = []
        for provider_key, provider_regions in catalog.items():
            for row in provider_regions:
                rows.append({'provider': provider_key, **row})
        return Response(rows)


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
        from apps.deployments.models import Service

        service_id = request.query_params.get('service_id')
        if service_id:
            try:
                service = Service.objects.get(id=service_id, owner=request.user)
            except Service.DoesNotExist:
                return Response(
                    {'error': 'Service not found or access denied.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            cpu = float(service.cpu_cores or 1)
            memory_gb = float(service.memory_mb or 512) / 1024
            estimates = advisor.estimate_monthly_cost(cpu, memory_gb)
            normalized = {k: float(v) for k, v in estimates.items()}
            cheapest = min(normalized, key=normalized.get) if normalized else None

            recommendation = advisor.ai_cost_analysis({
                'cpu_cores': cpu,
                'memory_mb': float(service.memory_mb or 512),
                'stack': str(service.deploy_type or '').lower(),
                'provider': str(service.provider.provider_type if service.provider else 'unknown'),
            })

            return Response({
                'scope': 'service',
                'service_id': str(service.id),
                'service_name': service.name,
                'estimates': normalized,
                'cheapest_provider': cheapest,
                'ai_recommendations': recommendation,
            })

        services = Service.objects.filter(owner=request.user)
        if not services.exists():
            baseline = advisor.estimate_monthly_cost(1.0, 0.5)
            normalized = {k: float(v) for k, v in baseline.items()}
            cheapest = min(normalized, key=normalized.get) if normalized else None
            return Response({
                'scope': 'workspace',
                'service_count': 0,
                'estimates': normalized,
                'cheapest_provider': cheapest,
                'ai_recommendations': advisor.ai_cost_analysis({
                    'cpu_cores': 1,
                    'memory_mb': 512,
                    'stack': 'mixed',
                    'provider': 'unknown',
                }),
            })

        totals: dict[str, Decimal] = {}
        total_cpu = 0.0
        total_memory_mb = 0.0

        for svc in services:
            svc_cpu = float(svc.cpu_cores or 1)
            svc_memory_mb = float(svc.memory_mb or 512)
            total_cpu += svc_cpu
            total_memory_mb += svc_memory_mb
            estimate = advisor.estimate_monthly_cost(svc_cpu, svc_memory_mb / 1024)
            for provider_name, amount in estimate.items():
                totals[provider_name] = totals.get(provider_name, Decimal("0.00")) + amount

        normalized = {k: float(v.quantize(Decimal("0.01"))) for k, v in totals.items()}
        cheapest = min(normalized, key=normalized.get) if normalized else None
        return Response({
            'scope': 'workspace',
            'service_count': services.count(),
            'total_cpu_cores': round(total_cpu, 2),
            'total_memory_mb': round(total_memory_mb, 2),
            'estimates': normalized,
            'cheapest_provider': cheapest,
            'ai_recommendations': advisor.ai_cost_analysis({
                'cpu_cores': total_cpu,
                'memory_mb': total_memory_mb,
                'stack': 'mixed',
                'provider': 'multi',
            }),
        })

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

        try:
            response, provider_name = ask_with_fallback(
                prompt=f"{context}\nUser: {message}",
                system_prompt=SYSTEM_PROMPT
            )
            return Response({'response': response, 'provider': provider_name})
        except Exception as exc:  # noqa: BLE001
            # Fail-open: keep assistant UI functional even if provider
            # discovery or upstream AI APIs are temporarily unavailable.
            logger.exception("Cloud intelligence chat degraded: %s", exc)
            return Response({
                'response': (
                    "Intelligence is temporarily degraded after deploy. "
                    "Retry in a few seconds or check AI provider settings."
                ),
                'provider': 'Mock AI (degraded)',
                'degraded': True,
            })

    @action(detail=False, methods=['get'])
    def providers(self, request):
        """List available AI providers and their status."""
        try:
            return Response(get_available_providers())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Cloud intelligence providers degraded: %s", exc)
            return Response([])

    @action(detail=False, methods=['post'])
    def troubleshoot(self, request):
        """
        Troubleshoot a specific deployment or service error.
        M-5: Deep diagnostic tool.
        """
        from apps.deployments.models import Deployment, Service

        deployment_id = request.data.get('deployment_id')
        service_id = request.data.get('service_id')
        error_trace = str(
            request.data.get('error_trace')
            or request.data.get('logs')
            or ''
        ).strip()

        if not error_trace and deployment_id:
            try:
                deploy = Deployment.objects.get(id=deployment_id, service__owner=request.user)
                error_trace = (deploy.build_logs or '').strip()
            except Deployment.DoesNotExist:
                return Response(
                    {'error': 'Deployment not found or access denied.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if not error_trace and service_id:
            try:
                service = Service.objects.get(id=service_id, owner=request.user)
            except Service.DoesNotExist:
                return Response(
                    {'error': 'Service not found or access denied.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            latest_failed = (
                service.deployments
                .filter(status=Deployment.Status.FAILED)
                .order_by('-created_at')
                .first()
            )
            if latest_failed:
                error_trace = (latest_failed.build_logs or '').strip()

        if not error_trace:
            return Response(
                {'error': 'error_trace (or deployment_id/service_id with logs) is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        analyzer = LogAnalyzer()
        issues = analyzer.analyze_logs(error_trace)
        diagnosis = analyzer.generate_diagnosis(error_trace)

        provider = None
        if diagnosis.startswith('[') and '] ' in diagnosis:
            provider = diagnosis.split(']')[0].lstrip('[')
            diagnosis = diagnosis.split('] ', 1)[1]

        remediator = RemediationEngine()
        suggested_actions = []
        for issue in issues:
            recommendation = remediator.suggest_fix(issue.get('type', ''))
            if recommendation:
                suggested_actions.append({
                    'issue_type': issue.get('type'),
                    'action': recommendation.get('action'),
                    'message': recommendation.get('message'),
                })

        confidence = max((float(i.get('confidence', 0.0)) for i in issues), default=0.35)
        root_cause = issues[0]['type'] if issues else 'UNKNOWN'

        return Response({
            "root_cause": root_cause,
            "confidence": confidence,
            "diagnosis": diagnosis,
            "provider": provider,
            "issues": issues,
            "suggested_actions": suggested_actions,
        })

    @action(detail=False, methods=['post'])
    def generate_iac(self, request):
        """
        Generate Infrastructure as Code (Terraform/Pulumi) from natural language description.
        """
        description = str(request.data.get('description') or '').strip()
        if not description:
            return Response(
                {'error': 'description is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_cloud = str(request.data.get('cloud') or 'aws').strip().lower()
        provider_map = {
            'aws': 'aws',
            'gcp': 'google',
            'google': 'google',
            'azure': 'azurerm',
        }
        provider = provider_map.get(target_cloud, 'aws')

        slug = re.sub(r'[^a-z0-9]+', '-', description.lower()).strip('-')[:32] or 'workload'
        resource_name = slug.replace('-', '_')

        resource_block = (
            f'resource "{provider}_instance" "{resource_name}" {{\n'
            f'  name = "{slug}"\n'
            '}\n'
        )

        lowered = description.lower()
        if any(word in lowered for word in ('bucket', 'storage', 'object store', 's3')):
            if provider == 'google':
                resource_block = (
                    f'resource "google_storage_bucket" "{resource_name}" {{\n'
                    f'  name     = "{slug}"\n'
                    '  location = var.region\n'
                    '}\n'
                )
            elif provider == 'azurerm':
                resource_block = (
                    f'resource "azurerm_storage_account" "{resource_name}" {{\n'
                    f'  name                     = "{resource_name[:24]}"\n'
                    '  location                 = var.region\n'
                    '  resource_group_name      = var.resource_group_name\n'
                    '  account_tier             = "Standard"\n'
                    '  account_replication_type = "LRS"\n'
                    '}\n'
                )
            else:
                resource_block = (
                    f'resource "aws_s3_bucket" "{resource_name}" {{\n'
                    f'  bucket = "{slug}"\n'
                    '}\n'
                )
        elif any(word in lowered for word in ('postgres', 'mysql', 'database', 'db')):
            if provider == 'google':
                resource_block = (
                    f'resource "google_sql_database_instance" "{resource_name}" {{\n'
                    f'  name             = "{slug}"\n'
                    '  database_version = "POSTGRES_15"\n'
                    '  region           = var.region\n'
                    '  settings { tier = "db-f1-micro" }\n'
                    '}\n'
                )
            elif provider == 'azurerm':
                resource_block = (
                    f'resource "azurerm_postgresql_flexible_server" "{resource_name}" {{\n'
                    f'  name                   = "{slug}"\n'
                    '  resource_group_name    = var.resource_group_name\n'
                    '  location               = var.region\n'
                    '  sku_name               = "B_Standard_B1ms"\n'
                    '  administrator_login    = var.db_admin\n'
                    '  administrator_password = var.db_password\n'
                    '}\n'
                )
            else:
                resource_block = (
                    f'resource "aws_db_instance" "{resource_name}" {{\n'
                    '  allocated_storage    = 20\n'
                    '  engine               = "postgres"\n'
                    '  instance_class       = "db.t3.micro"\n'
                    f'  identifier           = "{slug}"\n'
                    '  username             = var.db_admin\n'
                    '  password             = var.db_password\n'
                    '  skip_final_snapshot  = true\n'
                    '}\n'
                )
        elif any(word in lowered for word in ('kubernetes', 'k8s', 'cluster')):
            if provider == 'google':
                resource_block = (
                    f'resource "google_container_cluster" "{resource_name}" {{\n'
                    f'  name     = "{slug}"\n'
                    '  location = var.region\n'
                    '  initial_node_count = 1\n'
                    '}\n'
                )
            elif provider == 'azurerm':
                resource_block = (
                    f'resource "azurerm_kubernetes_cluster" "{resource_name}" {{\n'
                    f'  name                = "{slug}"\n'
                    '  location            = var.region\n'
                    '  resource_group_name = var.resource_group_name\n'
                    '  dns_prefix          = "aks"\n'
                    '  default_node_pool { name = "default" node_count = 1 vm_size = "Standard_B2s" }\n'
                    '  identity { type = "SystemAssigned" }\n'
                    '}\n'
                )
            else:
                resource_block = (
                    f'resource "aws_eks_cluster" "{resource_name}" {{\n'
                    f'  name = "{slug}"\n'
                    '  role_arn = var.eks_role_arn\n'
                    '  vpc_config { subnet_ids = var.subnet_ids }\n'
                    '}\n'
                )

        iac_code = (
            'terraform {\n'
            '  required_version = ">= 1.5.0"\n'
            '}\n\n'
            f'provider "{provider}" {{\n'
            '  region = var.region\n'
            '}\n\n'
            'variable "region" {\n'
            '  type = string\n'
            '}\n\n'
            + resource_block
        )

        return Response({'code': iac_code, 'language': 'hcl', 'provider': provider})

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
    def ecosystem_bulk_env(self, request):
        """
        Set environment variables across multiple services at once.
        """
        env_vars = request.data.get('env_vars')
        service_ids = request.data.get('service_ids')

        if not isinstance(env_vars, dict) or not isinstance(service_ids, list):
            return Response(
                {'error': 'env_vars (dict) and service_ids (list) are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.models import Service, EnvironmentVariable

        services = Service.objects.filter(id__in=service_ids, owner=request.user)
        if not services.exists():
            return Response(
                {'error': 'No matching services found or access denied'},
                status=status.HTTP_404_NOT_FOUND,
            )

        updated_count = 0
        for service in services:
            for key, value in env_vars.items():
                key_upper = str(key).strip().upper()
                if not key_upper:
                    continue

                # Simple heuristic for secrets
                is_secret = any(hint in key_upper for hint in ("KEY", "SECRET", "PASSWORD", "TOKEN", "DSN"))

                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key_upper,
                    defaults={'value': str(value), 'is_secret': is_secret}
                )
            updated_count += 1

        return Response({
            'status': 'success',
            'services_updated': updated_count,
            'keys_set': len(env_vars)
        })

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
