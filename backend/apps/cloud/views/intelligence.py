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

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser])
    def ecosystem_prompts(self, request):
        """
        Debug endpoint to show all prompts used in ecosystem analysis.

        ADMIN-ONLY: the prompts describe the platform's internal AI
        architecture (system prompt text, provider chains, validation
        rules) — disclosing them to every authenticated user was an
        information leak for anyone probing the platform's AI design.
        This helps with transparency and debugging AI behavior.
        """
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin access required.'},
                status=status.HTTP_403_FORBIDDEN,
            )
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

        # Guard: no concurrent active scan or deploy — but only trust the
        # flag for RECENT plans. A ghost task (worker lost the message)
        # left plans stuck in 'deploying' for DAYS, returning 429 on every
        # new scan and locking the operator out of the ecosystem UI
        # entirely (2026-09-02 incident). Plans older than 30 minutes
        # are auto-failed here rather than blocking; the beat task
        # recover_stale_ecosystem_plans is the scheduled safety net.
        from django.utils import timezone as _tz
        from datetime import timedelta as _td
        _recent_cutoff = _tz.now() - _td(minutes=30)
        _stale = EcosystemPlan.objects.filter(
            user=request.user,
            status__in=['scanning', 'deploying'],
            created_at__lt=_recent_cutoff,
        )
        for _ghost in _stale:
            # Capture the ORIGINAL status before mutating — otherwise the
            # audit message always says "stuck in 'failed'" (the value we
            # just wrote), losing the forensic record of what it was doing.
            _ghost_old_status = _ghost.status
            _ghost.status = 'failed'
            _ghost.error_message = (
                f"Auto-recovered at scan time: plan was stuck in "
                f"'{_ghost_old_status}' since {_ghost.created_at.isoformat()} "
                f"(>30 min). Cleared to unblock the ecosystem scan."
            )
            _ghost.save(update_fields=['status', 'error_message', 'updated_at'])

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
            from apps.deployments.models.service import Project
            try:
                project = Project.objects.get(id=project_id)
                if project.owner != request.user:
                    from apps.organizations.models.project import ProjectMember
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
        from apps.deployments.utils.env_sanitizer import (
            sanitize_env_value,
            is_placeholder,
        )

        services = Service.objects.filter(id__in=service_ids, owner=request.user)
        if not services.exists():
            return Response(
                {'error': 'No matching services found or access denied'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Scrub every value once at the door so the loop below never has
        # to reason about AI / template leakage per service.
        sanitized_env_vars: dict[str, str] = {}
        dropped_keys: list[str] = []
        for key, value in env_vars.items():
            key_upper = str(key).strip().upper()
            if not key_upper:
                continue
            cleaned = sanitize_env_value(value, key=key_upper, allow_empty=True)
            if cleaned is None or is_placeholder(cleaned):
                logger.warning(
                    "[ENV-SANITIZE] Dropping bulk placeholder value for %s",
                    key_upper,
                )
                dropped_keys.append(key_upper)
                continue
            sanitized_env_vars[key_upper] = cleaned

        updated_count = 0
        for service in services:
            for key_upper, value in sanitized_env_vars.items():
                from apps.cloud.services.build_constants import is_secret_env_var
                is_secret = is_secret_env_var(key_upper)

                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key_upper,
                    defaults={'value': str(value), 'is_secret': is_secret}
                )
            updated_count += 1

        return Response({
            'status': 'success',
            'services_updated': updated_count,
            'keys_set': len(sanitized_env_vars),
            'dropped_keys': dropped_keys,
        })

    @action(detail=False, methods=['post'], throttle_classes=[UserRateThrottle])
    def ecosystem_deploy(self, request):
        """
        Deploy a previously generated ecosystem plan.

        All services created are scoped to the plan's project for
        isolation, permissions, and resource tracking.
        """
        from apps.deployments.models.service import Project
        from apps.deployments.models.ecosystem import EcosystemPlan
        from apps.deployments.tasks.ecosystem import ecosystem_deploy_task

        plan_id = request.data.get('plan_id')
        plan = request.data.get('plan')
        project_id = request.data.get('project_id')
        use_shared_addons = request.data.get('use_shared_addons', True)
        cancel_others_on_failure = request.data.get('cancel_others_on_failure', False)
        shared_addon_config = request.data.get('shared_addon_config', {})
        env_scan_depth = request.data.get('env_scan_depth')
        if not isinstance(plan, dict):
            return Response(
                {'error': 'plan (object) is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Inject scan depth override into plan for the task
        if env_scan_depth in ('shallow', 'standard', 'deep'):
            plan['env_scan_depth'] = env_scan_depth

        # ── Concurrent-deploy guard (mirror of the scan view's 429) ────
        # Without this, a double-click or retry-after-timeout dispatches
        # two ecosystem_deploy_task instances that race through service
        # and deployment creation. The task has its own idempotency guard,
        # but rejecting at the view is cheaper and gives the user a clear
        # message instead of a silently-duplicated deploy.
        if EcosystemPlan.objects.filter(
            user=request.user,
            status__in=[
                EcosystemPlan.Status.SCANNING,
                EcosystemPlan.Status.DEPLOYING,
            ],
        ).exclude(id=plan_id).exists():
            return Response(
                {
                    'error': (
                        'Another ecosystem scan or deploy is already in '
                        'progress. Wait for it to finish or cancel it '
                        'before starting a new one.'
                    )
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Resolve project — from explicit param, or from existing plan
        project = None
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                if project.owner != request.user:
                    from apps.organizations.models.project import ProjectMember
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
            from apps.deployments.tasks.ecosystem.helpers.service import _ecosystem_project_name
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
            plan_record.save(update_fields=['plan', 'status', 'use_shared_addons', 'cancel_others_on_failure', 'shared_addon_config', 'project', 'updated_at'])
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

    @action(detail=False, methods=['post'])
    def ecosystem_add_service(self, request):
        """
        Add a custom service to the ecosystem plan.

        Generates docker-compose.yml, .env.production, and SPIFFE config
        for the new service and returns it to be added to the plan.
        """
        service_name = request.data.get('name', '').strip()
        port = request.data.get('port')
        stack = request.data.get('stack', 'python')
        directory = request.data.get('directory', '')
        trust_domain = request.data.get('trust_domain', 'trulay.co')

        if not service_name:
            return Response({'error': 'Service name is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not port:
            return Response({'error': 'Port is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            port = int(port)
        except (TypeError, ValueError):
            return Response({'error': 'Port must be a number'}, status=status.HTTP_400_BAD_REQUEST)

        # Derive names
        service_name = service_name.lower().replace(' ', '-').replace('_', '-')
        service_upper = service_name.upper().replace('-', '_')
        container_name = f"smsly-{service_name}"
        spiffe_id = f"spiffe://{trust_domain}/service/{service_name}"
        if not directory:
            directory = f"{service_upper}/smsly-{service_name}"

        # Generate docker-compose.yml
        docker_compose = f"""# =============================================================================
# SMSLY {service_upper} - Production Docker Compose
# =============================================================================
# mTLS: All inter-service communication via SPIFFE/SPIRE

services:
  {service_name}:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: {container_name}
    restart: unless-stopped
    ports:
      - "{port}:{port}"
    environment:
      - DATABASE_URL=${{DATABASE_URL}}
      - PORT={port}
      - ENVIRONMENT=production
      - LOG_LEVEL=INFO

      # SPIFFE/SPIRE mTLS
      - SPIFFE_TRUST_DOMAIN=${{SPIFFE_TRUST_DOMAIN}}
      - SPIFFE_ENDPOINT_SOCKET=unix:///opt/spire/run/agent.sock
      - FEATURE_SPIFFE_MTLS=true
      - SPIFFE_MTLS_STRICT_MODE=true
      - SPIFFE_HMAC_FALLBACK=false
      - CALLER_SVID_VALIDATION=true
      - MIGRATION_PHASE=phase4
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:{port}/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    volumes:
      - spire_agent_socket:/opt/spire/run:ro
    networks:
      - smsly-network
    labels:
      - "com.smsly.service={service_name}"

volumes:
  spire_agent_socket:
    external: true
    name: spire-agent-socket

networks:
  smsly-network:
    driver: bridge
    name: smsly-network
"""

        # Generate .env.production
        env_production = f"""# =============================================================================
# SMSLY {service_upper} - Production Configuration
# =============================================================================

ENVIRONMENT=production
LOG_LEVEL=INFO
PORT={port}

# PostgreSQL
DATABASE_URL=${{DATABASE_URL}}

# Secrets (generate with: python -c "import secrets; print(secrets.token_urlsafe(64))")
SECRET_KEY=${{SECRET_KEY}}
HMAC_SECRET=${{HMAC_SECRET}}

# Inter-service URLs (internal Docker network)
SECURITY_GATEWAY_URL=${{SECURITY_GATEWAY_URL}}
BACKEND_URL=${{BACKEND_URL}}
PLATFORM_API_URL=${{PLATFORM_API_URL}}
IDENTITY_SERVICE_URL=${{IDENTITY_SERVICE_URL}}
AUDIT_SERVICE_URL=${{AUDIT_SERVICE_URL}}
RATE_LIMIT_SERVICE_URL=${{RATE_LIMIT_SERVICE_URL}}

# SPIFFE/SPIRE mTLS
SPIFFE_TRUST_DOMAIN=${{SPIFFE_TRUST_DOMAIN}}
SPIFFE_ENDPOINT_SOCKET=unix:///opt/spire/run/agent.sock
FEATURE_SPIFFE_MTLS=true
SPIFFE_MTLS_STRICT_MODE=true
SPIFFE_HMAC_FALLBACK=false
CALLER_SVID_VALIDATION=true
MIGRATION_PHASE=phase4
"""

        # Generate SPIFFE entry
        spiffe_entry = {
            "spiffe_id": {
                "trust_domain": trust_domain,
                "path": f"service/{service_name}"
            },
            "parent_id": {
                "trust_domain": trust_domain,
                "path": "/spire-server"
            },
            "selectors": [
                {
                    "type": "docker",
                    "value": f"label:com.smsly.service={service_name}"
                }
            ],
            "x509_svid_ttl": "1h"
        }

        # Build the service plan entry
        service_plan = {
            "repo": f"custom/{service_name}",
            "name": service_name,
            "stack": stack,
            "port": port,
            "build": "dockerfile",
            "addons": ["POSTGRES"],
            "env_vars": {
                "PORT": str(port),
                "ENVIRONMENT": "production",
                "SPIFFE_TRUST_DOMAIN": trust_domain,
            },
            "depends_on": [],
            "deploy_order": 0,
            "skip": False,
            "_custom": True,
            "_directory": directory,
            "_docker_compose": docker_compose,
            "_env_production": env_production,
            "_spiffe_entry": spiffe_entry,
        }

        return Response({
            'service': service_plan,
            'checklist': {
                'docker_compose': f'{directory}/docker-compose.yml',
                'env_production': f'{directory}//.env.production',
                'spiffe_entry': spiffe_entry,
                'services_to_update': [
                    'gateway', 'platform-api', 'backend', 'identity',
                    'audit', 'policy', 'rate-limit', 'email', 'transaction-chain'
                ],
            }
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
                from apps.cloud.services.build_constants import is_secret_env_var
                masked_env[k] = '********' if is_secret_env_var(k) else v
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
            from apps.cloud.services.build_constants import is_secret_env_var
            masked_shared[k] = '********' if is_secret_env_var(k) else v

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

    @action(detail=False, methods=['get'], url_path='plans')
    def list_plans(self, request):
        """List user's ecosystem plans with optional status filter."""
        from apps.deployments.models.ecosystem import EcosystemPlan
        from apps.cloud.serializers import EcosystemPlanSummarySerializer

        qs = EcosystemPlan.objects.filter(user=request.user)
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(
                EcosystemPlanSummarySerializer(page, many=True).data
            )
        return Response(EcosystemPlanSummarySerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path=r'plans/(?P<plan_id>[^/.]+)')
    def plan_detail(self, request, plan_id=None):
        """Retrieve a single ecosystem plan by ID."""
        from apps.deployments.models.ecosystem import EcosystemPlan
        from apps.cloud.serializers import EcosystemPlanDetailSerializer

        try:
            plan = EcosystemPlan.objects.get(id=plan_id, user=request.user)
        except EcosystemPlan.DoesNotExist:
            return Response(
                {'error': 'Plan not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(EcosystemPlanDetailSerializer(plan).data)

    @action(detail=False, methods=['post'], url_path=r'plans/(?P<plan_id>[^/.]+)/restore-snapshots')
    def restore_snapshots(self, request, plan_id=None):
        """Restore pre-ecosystem-deploy snapshots for a finished plan.

        Iterates the plan's ``services_created`` entries, restoring each
        linked ``pre_deploy_snapshot_id`` to its service (config + env +
        DB clone, mirroring the single-snapshot restore endpoint).
        Only ``failed``/``completed`` plans; refuses while scanning,
        review, or deploying. Requires explicit ``confirm: true``.
        Body: {"confirm": true, "service_ids"?: [...], "redeploy"?: bool
        (default true — restored config only takes effect on redeploy).}
        """
        import contextlib

        from apps.cloud.models.backup import ServiceSnapshot
        from apps.deployments.models.audit import AuditLog
        from apps.deployments.models.ecosystem import EcosystemPlan
        from apps.deployments.services.snapshot_service import SnapshotService
        from apps.deployments.views.snapshot import ServiceSnapshotViewSet

        if str(request.data.get('confirm', '')).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            plan = EcosystemPlan.objects.get(id=plan_id, user=request.user)
        except EcosystemPlan.DoesNotExist:
            return Response(
                {'error': 'Plan not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if plan.status not in (EcosystemPlan.Status.FAILED, EcosystemPlan.Status.COMPLETED):
            return Response(
                {'error': f'Plan is {plan.status}; restore is only available for failed or completed plans.'},
                status=status.HTTP_409_CONFLICT,
            )

        only_ids = request.data.get('service_ids')
        if only_ids is not None and not isinstance(only_ids, list):
            return Response(
                {'error': 'service_ids must be a list of service IDs.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        only_set = {str(sid) for sid in (only_ids or [])}
        redeploy = request.data.get('redeploy', True)
        redeploy = str(redeploy).lower() not in ('false', '0', 'no', 'off')

        restored, skipped, errors = [], [], []
        for entry in (plan.services_created or []):
            if not isinstance(entry, dict):
                continue
            sid = str(entry.get('service_id') or '')
            name = entry.get('name') or sid or 'unknown'
            snap_id = entry.get('pre_deploy_snapshot_id')
            if not sid or not snap_id:
                skipped.append({'service_id': sid or None, 'service_name': name,
                                'reason': 'no pre-deploy snapshot (new service)'})
                continue
            if only_set and sid not in only_set:
                skipped.append({'service_id': sid, 'service_name': name,
                                'reason': 'not selected'})
                continue
            try:
                snap = ServiceSnapshot.objects.select_related('service').get(id=snap_id)
            except ServiceSnapshot.DoesNotExist:
                errors.append({'service_id': sid, 'service_name': name,
                               'error': 'linked snapshot no longer exists'})
                continue
            if str(snap.service_id) != sid:
                errors.append({'service_id': sid, 'service_name': name,
                               'error': 'snapshot belongs to a different service'})
                continue
            if not ServiceSnapshotViewSet._user_can_access_service(request.user, snap.service):
                errors.append({'service_id': sid, 'service_name': name,
                               'error': 'permission denied for target service'})
                continue
            try:
                result = SnapshotService.restore_snapshot(
                    snapshot_id=str(snap.id),
                    redeploy=redeploy,
                    requesting_user=request.user,
                )
                with contextlib.suppress(Exception):
                    AuditLog(
                        actor=request.user.get_username(),
                        action='SNAPSHOT_RESTORED',
                        target=f'snapshot={snap.id}',
                        metadata={
                            'service_id': sid,
                            'ecosystem_plan_id': str(plan.id),
                            'redeploy': redeploy,
                            'changes_count': result.get('config_changes', 0),
                        },
                    ).save()
                restored.append({
                    'service_id': sid,
                    'service_name': snap.service.name if snap.service else name,
                    'snapshot_id': str(snap.id),
                    'snapshot_label': snap.label,
                    'config_changes': result.get('config_changes', 0),
                    'env_var_changes': result.get('env_var_changes', 0),
                    'db_clone_restored': result.get('db_clone_restored', False),
                    'redeployed': result.get('redeployed', False),
                })
            except Exception as exc:
                errors.append({'service_id': sid, 'service_name': name,
                               'error': str(exc)})
        return Response({
            'plan_id': str(plan.id),
            'redeploy': redeploy,
            'restored': restored,
            'skipped': skipped,
            'errors': errors,
        })
