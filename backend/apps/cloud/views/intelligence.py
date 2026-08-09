"""Views module."""
import logging
import re
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.cloud.models import CloudProvider, CloudResource
from apps.cloud.serializers import (
    CloudProviderCreateSerializer,
    CloudProviderSerializer,
    CloudResourceSerializer,
)

logger = logging.getLogger(__name__)

from .providers import _strip_literal_credentials


class IntelligencePayloadSerializer(serializers.Serializer):
    data = serializers.JSONField(required=False)  # type: ignore[assignment]


class EcosystemBulkEnvRateThrottle(UserRateThrottle):
    scope = 'ecosystem_bulk_env'


class IntelligenceViewSet(viewsets.GenericViewSet):
    """
    AI-powered cloud optimization and debugging.
    """
    serializer_class = IntelligencePayloadSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'])
    def analyze_logs(self, request):
        """Analyze logs for errors and anomalies."""
        from apps.intelligence.analyzer import LogAnalyzer
        logs = request.data.get('logs', [])
        # service_id = request.data.get('service_id')

        analyzer = LogAnalyzer()
        # analyzer.analyze_logs expects a string, not a list
        if isinstance(logs, list):
            logs = "\n".join(str(item) for item in logs)
        elif not isinstance(logs, str):
            logs = str(logs)

        analysis = analyzer.analyze_logs(logs)

        return Response(analysis)

    @action(detail=False, methods=['post'])
    def suggest_remediation(self, request):
        """Suggest fixes for a specific error."""
        from apps.intelligence.remediator import RemediationEngine
        error_msg = request.data.get('error')

        if not error_msg:
            return Response(
                {'error': 'error field is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remediator = RemediationEngine()
        plan = remediator.suggest_fix(error_msg)

        if not plan:
            return Response(
                {'error': f'No known fix for issue type: {error_msg}'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(plan)

    @action(detail=False, methods=['get'])
    def optimize_cost(self, request):
        """Analyze current usage and suggest cost optimizations."""
        from apps.intelligence.cost import CostAdvisor
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
            from apps.intelligence.providers import SYSTEM_PROMPT, ask_with_fallback
            response, provider_name = ask_with_fallback(
                prompt=f"{context}\nUser: {message}",
                system_prompt=SYSTEM_PROMPT
            )
            return Response({'response': response, 'provider': provider_name})
        except Exception as exc:
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
            from apps.intelligence.providers import get_available_providers
            return Response(get_available_providers())
        except Exception as exc:
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

        from apps.intelligence.analyzer import LogAnalyzer
        from apps.intelligence.remediator import RemediationEngine
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

        iac_code = _strip_literal_credentials(iac_code)
        acknowledged = bool(request.data.get('requires_acknowledgement'))

        try:
            from apps.deployments.models_audit import AuditLog
            AuditLog(
                user=request.user if request.user.is_authenticated else None,
                actor=request.user.get_username() if request.user.is_authenticated else 'system',
                action='IAC_ACKNOWLEDGED' if acknowledged else 'IAC_PREVIEW_GENERATED',
                target=f'cloud:{provider}',
                metadata={
                    'cloud': provider,
                    'description_chars': len(description),
                    'acknowledged': acknowledged,
                },
            ).save()
        except Exception as exc:
            logger.warning("Failed to write IAC audit log: %s", exc)

        warning = (
            'WARNING: This is a preview generated from a natural-language '
            'description. Review and add required_providers, variables, and '
            'state backend before applying. Literal credentials have been '
            'replaced with var.* references; you must still declare those '
            'variables and supply values via a non-committed tfvars file.'
        )
        return Response({
            'code': iac_code,
            'language': 'hcl',
            'provider': provider,
            'template': iac_code,
            'preview_only': True,
            'acknowledged': acknowledged,
            'warning': warning,
        })

    @action(detail=False, methods=['get'])
    def ecosystem_prompts(self, request):
        """
        Debug endpoint to show all prompts used in ecosystem analysis.
        This helps with transparency and debugging AI behavior.
        """
        try:
            from apps.deployments.services.ecosystem import get_ecosystem_prompts

            prompts = get_ecosystem_prompts()

            # Add current timestamp and context
            response_data = {
                'timestamp': timezone.now().isoformat(),
                'user_id': request.user.id if request.user.is_authenticated else None,
                'prompts': prompts,
                'ai_providers_available': ['openai', 'anthropic', 'google', 'local'],  # Add your actual providers
                'ecosystem_prompt_rules': {
                    'strict_type_constraints': [
                        'All array fields must contain ONLY strings, NEVER objects/dicts',
                        '"depends_on" must be array of strings: ["service-1", "service-2"]',
                        '"shared_by" must be array of strings: ["repo-a", "repo-b"]',
                        'Service-level "addons" must be array of strings: ["POSTGRES", "REDIS"]',
                        '"deploy_sequence" must be array of strings: ["addons", "service-a"]',
                        '"env_vars" values must be strings only: {"KEY": "{{PLACEHOLDER}}"}'
                    ],
                    'critical_error_prevention': [
                        'No nested objects inside arrays',
                        'No unhashable types in string fields',
                        'All service names must be strings',
                        'All repo references must be strings'
                    ]
                }
            }

            return Response(response_data)

        except Exception as e:
            return Response(
                {'error': f'Failed to retrieve prompts: {e!s}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], throttle_classes=[UserRateThrottle])
    def ecosystem_scan(self, request):
        """
        Scan all accessible GitHub repositories and generate a zero-click deploy plan.

        Accepts optional ``project_id`` to scope the plan and all subsequently
        created services to a specific project for isolation and permissions.
        """
        from apps.deployments.models.ecosystem import EcosystemPlan
        from apps.deployments.tasks.ecosystem import ecosystem_scan_task

        # Guard: no concurrent active scan or deploy
        if EcosystemPlan.objects.filter(
            user=request.user,
            status__in=['scanning', 'deploying'],
        ).exists():
            return Response(
                {'error': 'A scan or deploy is already in progress.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        ai_provider = request.data.get('ai_provider')
        selected_repos = request.data.get('selected_repos')
        project_id = request.data.get('project_id')

        # Validate project access if provided
        project = None
        if project_id:
            from apps.deployments.models_core import Project
            try:
                project = Project.objects.get(id=project_id)
                if project.owner != request.user:
                    from apps.deployments.models_project import ProjectMember
                    is_member = ProjectMember.objects.filter(
                        project=project, user=request.user,
                    ).exists()
                    if not is_member:
                        return Response(
                            {'error': 'You do not have access to this project.'},
                            status=status.HTTP_403_FORBIDDEN,
                        )
            except Project.DoesNotExist:
                return Response(
                    {'error': 'Project not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        plan_record = EcosystemPlan.objects.create(
            user=request.user,
            project=project,
            selected_repos=selected_repos or [],
            ai_provider=ai_provider,
            status=EcosystemPlan.Status.SCANNING,
        )

        task = ecosystem_scan_task.delay(
            str(request.user.id),
            30,
            ai_provider=ai_provider,
            selected_repos=selected_repos,
            plan_id=str(plan_record.id),
            project_id=str(project.id) if project else None,
        )

        plan_record.scan_task_id = task.id
        plan_record.save(update_fields=['scan_task_id'])

        return Response({
            'task_id': task.id,
            'plan_id': str(plan_record.id),
            'project_id': str(project.id) if project else None,
            'status': 'scanning',
        })

    @action(detail=False, methods=['post'], throttle_classes=[EcosystemBulkEnvRateThrottle])
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

        from apps.deployments.models import EnvironmentVariable, Service

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
                is_secret = any(hint in key_upper for hint in ("KEY", "SECRET", "PASSWORD", "TOKEN", "DSN", "_URL", "_URI"))

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

    @action(detail=False, methods=['post'], throttle_classes=[UserRateThrottle])
    def ecosystem_deploy(self, request):
        """
        Deploy a previously generated ecosystem plan.

        All services created are scoped to the plan's project for
        isolation, permissions, and resource tracking.
        """
        from apps.deployments.models_core import Project
        from apps.deployments.models.ecosystem import EcosystemPlan
        from apps.deployments.tasks.ecosystem import ecosystem_deploy_task

        plan_id = request.data.get('plan_id')
        plan = request.data.get('plan')
        project_id = request.data.get('project_id')
        use_shared_addons = request.data.get('use_shared_addons', True)
        cancel_others_on_failure = request.data.get('cancel_others_on_failure', False)
        shared_addon_config = request.data.get('shared_addon_config', {})
        if not isinstance(plan, dict):
            return Response(
                {'error': 'plan (object) is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve project — from explicit param, or from existing plan
        project = None
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                if project.owner != request.user:
                    from apps.deployments.models_project import ProjectMember
                    if not ProjectMember.objects.filter(project=project, user=request.user).exists():
                        return Response(
                            {'error': 'You do not have access to this project.'},
                            status=status.HTTP_403_FORBIDDEN,
                        )
            except Project.DoesNotExist:
                return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            plan_record = EcosystemPlan.objects.get(id=plan_id, user=request.user)
            if not project and plan_record.project:
                project = plan_record.project
            elif project and not plan_record.project:
                plan_record.project = project
        except EcosystemPlan.DoesNotExist:
            plan_record = None

        if not project:
            from apps.deployments.tasks.ecosystem import _ecosystem_project_name
            services_plan = plan.get("services", []) if isinstance(plan, dict) else []
            raw_name = str(
                (plan.get("project_name") if isinstance(plan, dict) else None)
                or (plan.get("name") if isinstance(plan, dict) else None)
                or (services_plan[0].get("repo", "").split("/")[-1] if services_plan and isinstance(services_plan, list) and services_plan[0].get("repo") else "")
                or "Ecosystem Cluster"
            ).strip()
            if not raw_name:
                raw_name = "Ecosystem Cluster"
            proj_name = _ecosystem_project_name(raw_name)[:100]
            project = Project.objects.create(
                owner=request.user,
                name=proj_name,
                description="Auto-created by zero-config ecosystem deployment.",
                is_ephemeral=True,
            )

        if plan_record:
            if not plan_record.project:
                plan_record.project = project
            plan['use_shared_addons'] = use_shared_addons
            plan['cancel_others_on_failure'] = cancel_others_on_failure
            plan['shared_addon_config'] = shared_addon_config
            plan_record.plan = plan
            plan_record.status = EcosystemPlan.Status.DEPLOYING
            plan_record.use_shared_addons = use_shared_addons
            plan_record.cancel_others_on_failure = cancel_others_on_failure
            plan_record.shared_addon_config = shared_addon_config
            plan_record.save(update_fields=['plan', 'status', 'use_shared_addons', 'cancel_others_on_failure', 'shared_addon_config', 'updated_at'])
        else:
            plan_record = EcosystemPlan.objects.create(
                user=request.user,
                project=project,
                plan=plan,
                status=EcosystemPlan.Status.DEPLOYING,
                use_shared_addons=use_shared_addons,
                cancel_others_on_failure=cancel_others_on_failure,
                shared_addon_config=shared_addon_config,
            )

        task = ecosystem_deploy_task.delay(
            str(request.user.id),
            plan,
            plan_id=str(plan_record.id),
            project_id=str(project.id) if project else None,
        )

        plan_record.deploy_task_id = task.id
        plan_record.save(update_fields=['deploy_task_id'])

        return Response({
            'task_id': task.id,
            'plan_id': str(plan_record.id),
            'project_id': str(project.id) if project else None,
            'status': 'deploying',
        })

    @action(detail=False, methods=['get'])
    def task_status(self, request):
        """
        Check status of a long-running background task (Celery).
        """
        from apps.deployments.models.ecosystem import EcosystemPlan

        task_id = request.query_params.get('task_id')
        if not task_id:
            return Response({'error': 'task_id required'}, status=status.HTTP_400_BAD_REQUEST)

        import json

        from celery.result import AsyncResult
        result = AsyncResult(task_id)

        # Stale detection: if the task isn't ready but the associated plan
        # has been in SCANNING for >40 minutes, the worker likely died.
        STALE_THRESHOLD_SECONDS = 2400  # 40 min (> Celery time_limit of 35 min)
        if not result.ready():
            stale_plan = EcosystemPlan.objects.filter(
                scan_task_id=task_id,
                user=request.user,
                status=EcosystemPlan.Status.SCANNING,
                updated_at__lt=timezone.now() - timezone.timedelta(seconds=STALE_THRESHOLD_SECONDS),
            ).first()
            if stale_plan:
                stale_plan.status = EcosystemPlan.Status.FAILED
                stale_plan.error_message = (
                    "Scan was interrupted (system shutdown or worker crash). "
                    "Please start a new scan."
                )
                stale_plan.save(update_fields=['status', 'error_message', 'updated_at'])
                try:
                    result.revoke(terminate=False)
                except Exception as exc:
                    logger.debug("Failed to revoke stale task %s: %s", task_id, exc)
                return Response({
                    'task_id': task_id,
                    'status': 'FAILURE',
                    'error': stale_plan.error_message,
                    'result': {'error': stale_plan.error_message},
                })

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
                exception_type = payload.__class__.__name__
                message = str(payload) or exception_type
                if exception_type == "SoftTimeLimitExceeded":
                    message = "Background task timed out before it could finish. Retry with a smaller batch or try again later."
                payload = {
                    'error': message,
                    'exception_type': exception_type,
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
            'scan_progress': None,
        }
        if isinstance(payload, dict) and payload.get('error'):
            response_data['error'] = payload.get('error')

        # Include persisted scan progress in responses so the frontend
        # can show it when the user returns after navigating away.
        if not result.ready():
            plan_with_progress = EcosystemPlan.objects.filter(
                scan_task_id=task_id,
                user=request.user,
            ).values_list('scan_progress', flat=True).first()
            if plan_with_progress:
                response_data['scan_progress'] = plan_with_progress

        # Cache scan results and sync plan status
        if result.ready() and isinstance(payload, dict):
            if 'plan' in payload and not payload.get('error'):
                cache_key = f"ecosystem:scan:{request.user.id}"
                cache.set(cache_key, payload, timeout=1800)

            if result.status == 'SUCCESS':
                EcosystemPlan.objects.filter(scan_task_id=task_id, user=request.user).update(
                    status=EcosystemPlan.Status.REVIEW,
                    plan=payload,
                )
            elif result.status == 'FAILURE':
                EcosystemPlan.objects.filter(scan_task_id=task_id, user=request.user).update(
                    status=EcosystemPlan.Status.FAILED,
                    error_message=str(payload.get('error', '')),
                )
                EcosystemPlan.objects.filter(deploy_task_id=task_id, user=request.user).update(
                    status=EcosystemPlan.Status.FAILED,
                    error_message=str(payload.get('error', '')),
                )

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def cached_scan_result(self, request):
        """Return cached ecosystem scan result if available."""
        cache_key = f"ecosystem:scan:{request.user.id}"
        cached = cache.get(cache_key)
        if cached:
            return Response({'has_cache': True, 'plan': cached})
        return Response({'has_cache': False})

    @action(detail=False, methods=['get'])
    def active_plan(self, request):
        """Return the user's most recent non-completed plan for resume."""
        from apps.deployments.models.ecosystem import EcosystemPlan

        SCANNING_STALE_THRESHOLD = timezone.timedelta(minutes=40)

        plan = EcosystemPlan.objects.filter(
            user=request.user,
            status__in=['scanning', 'review', 'deploying'],
        ).first()

        if not plan:
            return Response({'has_active_plan': False})

        # If the plan has been SCANNING for >40 minutes, the worker likely
        # died (shutdown, crash, etc.). Mark it FAILED so the frontend
        # doesn't enter an infinite polling loop.
        if plan.status == 'scanning' and plan.updated_at:
            age = timezone.now() - plan.updated_at
            if age > SCANNING_STALE_THRESHOLD:
                plan.status = EcosystemPlan.Status.FAILED
                plan.error_message = (
                    "Previous scan was interrupted (system shutdown or worker crash). "
                    "Please start a new scan."
                )
                plan.save(update_fields=['status', 'error_message', 'updated_at'])
                return Response({'has_active_plan': False})

        return Response({
            'has_active_plan': True,
            'plan_id': str(plan.id),
            'status': plan.status,
            'scan_task_id': plan.scan_task_id,
            'deploy_task_id': plan.deploy_task_id,
            'selected_repos': plan.selected_repos,
            'ai_provider': plan.ai_provider,
            'plan': plan.plan,
            'scan_progress': plan.scan_progress,
        })

    @action(detail=False, methods=['get'])
    def download_env(self, request):
        """Download all env vars from the latest ecosystem plan as a JSON file."""
        from apps.deployments.models.ecosystem import EcosystemPlan
        from django.http import JsonResponse

        plan = EcosystemPlan.objects.filter(
            user=request.user,
            status__in=['review', 'deploying', 'completed'],
        ).order_by('-updated_at').first()

        if not plan:
            return Response(
                {'error': 'No ecosystem plan found. Run a scan first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        plan_data = plan.plan
        if not plan_data or 'services' not in plan_data:
            return Response(
                {'error': 'Plan has no services data.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Extract env vars per service with addon/service metadata
        services_export = {}
        for service in plan_data['services']:
            name = service.get('name', 'unknown')
            raw_env = (service.get('env_vars', {}) or {})
            # Mask secret values in download
            masked_env = {}
            for k, v in raw_env.items():
                key_upper = str(k).strip().upper()
                is_secret = any(hint in key_upper for hint in ("KEY", "SECRET", "PASSWORD", "TOKEN", "DSN", "_URL", "_URI"))
                masked_env[k] = '********' if is_secret else v
            services_export[name] = {
                'env_vars': masked_env,
                'addons': service.get('addons', []) or [],
                'depends_on': service.get('depends_on', []) or [],
                'port': service.get('port'),
                'stack': service.get('stack'),
                'build': service.get('build'),
                'repo': service.get('repo'),
                'skip': service.get('skip', False),
            }

        shared_env = plan_data.get('shared_env', {}) or {}
        masked_shared = {}
        for k, v in shared_env.items():
            key_upper = str(k).strip().upper()
            is_secret = any(hint in key_upper for hint in ("KEY", "SECRET", "PASSWORD", "TOKEN", "DSN", "_URL", "_URI"))
            masked_shared[k] = '********' if is_secret else v

        payload = {
            'plan_id': str(plan.id),
            'status': plan.status,
            'generated_at': plan.updated_at.isoformat() if plan.updated_at else None,
            'deploy_sequence': plan_data.get('deploy_sequence', []),
            'addons': plan_data.get('addons', []),
            'shared_env': masked_shared,
            'services': services_export,
        }

        response = JsonResponse(payload, json_dumps_params={'indent': 2})
        response['Content-Disposition'] = 'attachment; filename="ecosystem-env.json"'
        return response

    @action(detail=False, methods=['post'])
    def analyze_repo(self, request):
        """
        Analyzes a repository to determine stack, build method, and required ports/env vars.
        Returns stack, languages, port, build strategy, addons, suggested env vars.
        """
        import tempfile

        from apps.cloud.services.code_analyzer import (
            MAX_TOTAL_BYTES,
            iter_repo_files,
            walk_repo_with_cap,
        )
        from apps.cloud.services.git_manager import GitManager
        from apps.deployments.services.ecosystem import heuristic_analysis

        MAX_FILES = 500

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
                    logger.warning(
                        "Repo clone failed for %s: %s",
                        repo_url, e, exc_info=True,
                    )
                    return Response(
                        {'error': 'Repository not found or inaccessible.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Analyze
                walk = walk_repo_with_cap(project_path, MAX_TOTAL_BYTES)
                if walk.capped:
                    return Response(
                        {'error': 'Repo too large (>50MB total)'},
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                    )
                project_files = []
                for _abs, rel_path, _size in iter_repo_files(
                    project_path, MAX_TOTAL_BYTES,
                ):
                    if len(project_files) >= MAX_FILES:
                        break
                    project_files.append(rel_path)

                analysis_results = heuristic_analysis(project_files, clone_dir=project_path)

                return Response(analysis_results)

        except Exception as e:
            logger.error("Repo analysis failed: %s", e, exc_info=True)
            return Response(
                {'error': 'Analysis failed.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
