import os
import ast
import re
import glob

# 1. EXTRACT VIEWS
# ----------------
views_file_ops = [
    '_resolve_target_type', '_dispatch_file_operation', 'file_browse',
    '_k8s_file_browse', '_k8s_exec_file_op', '_exec_file_list',
    '_resolve_remote_server', '_parse_ls_output', 'file_download',
    '_local_file_download', 'file_delete', '_local_file_delete',
    'file_mkdir', '_local_file_mkdir', 'file_read', '_local_file_read',
    'file_write', '_local_file_write', 'file_upload', '_local_file_upload'
]

views_env_ops = ['env_vars', 'env_var_detail']
views_domain_ops = ['verify_domain', 'check_domain', 'retry_domain', 'add_domain', 'delete_domain', '_find_domain_conflict', '_enforce_custom_domain_quota']
views_ai_ops = ['ai_router_config']

views_service_ops = [
    'get_queryset', '_is_remote_sync_request', 'perform_create', 'perform_update', 'destroy',
    '_destroy_remote_sync', 'perform_destroy', 'retry_delete', 'hide_public_domain',
    'unhide_public_domain', 'deployments', 'stop', 'restart', 'recheck_health', 'status',
    'deploy', 'trigger_jules_fix', 'create_preview', 'list_previews', 'destroy_preview',
    'multi_deploy', 'instant_rollback', 'timeline', 'stats', 'get_permissions',
    'get_throttles', 'dependencies', 'bulk_action', 'sidebar', '_sync_caddy'
]

with open('backend/apps/deployments/views.py', 'r') as f:
    views_source = f.read()
views_lines = views_source.split('\n')
views_tree = ast.parse(views_source)

def get_node_source(tree, lines, func_names, class_name=None):
    res = {}
    for node in tree.body:
        if class_name and isinstance(node, ast.ClassDef) and node.name == class_name:
            for body_node in node.body:
                if isinstance(body_node, ast.FunctionDef) and body_node.name in func_names:
                    start = body_node.decorator_list[0].lineno if body_node.decorator_list else body_node.lineno
                    res[body_node.name] = '\n'.join(lines[start-1:body_node.end_lineno])
        elif not class_name and isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in func_names:
            start = node.decorator_list[0].lineno if hasattr(node, 'decorator_list') and node.decorator_list else node.lineno
            res[node.name] = '\n'.join(lines[start-1:node.end_lineno])
    return res

views_imports = []
for node in views_tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        views_imports.append(ast.unparse(node))
views_imports_str = '\n'.join(views_imports)

def build_view_file(filename, mixin_name, ops, global_funcs):
    out = ["import logging", "logger = logging.getLogger(__name__)", views_imports_str, "\n"]

    # inject CADDYFILE const for views_system
    if '_redact_caddyfile_preview' in global_funcs:
        out.append("_CADDYFILE_REDACT_KEYWORDS = (\n    'Strict-Transport-Security',\n    'tls',\n    'internal',\n    'basicauth',\n    'header Strict-Transport-Security',\n)\n")

    g_code = get_node_source(views_tree, views_lines, global_funcs)
    for g in global_funcs:
        if g in g_code:
            out.append(g_code[g])
            out.append("\n")

    if mixin_name:
        out.append(f"class {mixin_name}:")
        m_code = get_node_source(views_tree, views_lines, ops, 'ServiceViewSet')
        for m in ops:
            if m in m_code:
                out.append(m_code[m])
                out.append("\n")

    with open(f"backend/apps/deployments/{filename}", "w") as f:
        f.write('\n'.join(out))

build_view_file('views_files.py', 'ServiceFileActionsMixin', views_file_ops, ['CleanupFileResponse', '_backup_download_headers', '_verify_signed_download', '_generate_signed_download_url', '_parse_single_range', '_file_iterator', '_open_backup_download_response'])
build_view_file('views_envvars.py', 'ServiceEnvVarActionsMixin', views_env_ops, ['_is_valid_env_key', '_looks_masked_secret'])
build_view_file('views_domains.py', 'ServiceDomainActionsMixin', views_domain_ops, ['_normalize_request_domain', '_rewrite_public_domain', '_service_for_domain', '_parse_bool', 'DomainConfigView'])
build_view_file('views_ai_router.py', 'ServiceAIRouterActionsMixin', views_ai_ops, [])
build_view_file('views_deployment.py', None, [], ['DeploymentViewSet', 'RemoteTriggerView'])
build_view_file('views_backup.py', None, [], ['ServiceBackupViewSet', 'ServerBackupViewSet', 'BackupScheduleViewSet'])
build_view_file('views_system.py', None, [], ['SystemConfigView', 'PlatformResourcesView', 'RouteRecheckView', '_redact_caddyfile_preview'])

# ServiceViewSet file
sv_out = ["import logging", "logger = logging.getLogger(__name__)", views_imports_str,
          "from .views_files import ServiceFileActionsMixin",
          "from .views_envvars import ServiceEnvVarActionsMixin",
          "from .views_domains import ServiceDomainActionsMixin",
          "from .views_ai_router import ServiceAIRouterActionsMixin", "\n"]

g_funcs = ['_check_tier_gates_disabled', '_error_response', '_cancel_stale_in_progress_deployments', '_setup_provider_webhook', '_has_active_deployment', '_resolve_provider_for_service', '_is_local_deploy_target', '_resolve_local_provider', '_resolve_provider_for_target', '_resolve_requested_deploy_target']
g_code = get_node_source(views_tree, views_lines, g_funcs)
for g in g_funcs:
    if g in g_code:
        sv_out.append(g_code[g])
        sv_out.append("\n")

sv_out.append("class ServiceViewSet(ServiceFileActionsMixin, ServiceEnvVarActionsMixin, ServiceDomainActionsMixin, ServiceAIRouterActionsMixin, viewsets.ModelViewSet):")
sv_m_code = get_node_source(views_tree, views_lines, views_service_ops, 'ServiceViewSet')
for m in views_service_ops:
    if m in sv_m_code:
        sv_out.append(sv_m_code[m])
        sv_out.append("\n")

with open('backend/apps/deployments/views_service.py', 'w') as f:
    f.write('\n'.join(sv_out))

with open('backend/apps/deployments/views.py', 'w') as f:
    f.write('"""Views re-export shim"""\n')
    f.write('from .views_service import ServiceViewSet\n')
    f.write('from .views_deployment import DeploymentViewSet, RemoteTriggerView\n')
    f.write('from .views_backup import ServiceBackupViewSet, ServerBackupViewSet, BackupScheduleViewSet\n')
    f.write('from .views_system import SystemConfigView, PlatformResourcesView, RouteRecheckView, _redact_caddyfile_preview\n')
    f.write('from .views_domains import DomainConfigView\n')
    f.write('from .views_audit import AuditLogViewSet\n')
    f.write('from .views_auth import SessionTokenView, ZeroTrustHMACAuthentication, CaddySecretOrAdminPermission\n')
    f.write('from .views_route_status import RouteStatusView\n')


# 2. EXTRACT TASKS
# ----------------
tasks_mapping = {
    'tasks_utils': ['_env_bool', '_env_int', 'should_skip_review_for_commit_message', '_current_agent_node_queue'],
    'tasks_deploy': ['smart_deploy_task', 'resume_deploy_task', 'enqueue_smart_deploy_task', 'recover_stalled_queued_deployments', '_resolve_provider_for_service', '_deployment_effective_server', '_is_local_deployment_server', 'fleet_build_lock', '_run_managed_image_post_deploy_hooks', '_do_promote', '_deploy_container', '_post_deploy_monitor', '_handle_failure', 'delete_service_task'],
    'tasks_deploy_local': ['_docker_safe_segment', '_detect_exposed_port', '_coerce_int', '_is_legacy_default_healthcheck', '_build_platform_healthcheck', '_build_runtime_env', '_smart_derive_database_vars', '_smart_derive_redis_vars', '_infer_database_name', '_ensure_database_exists', '_is_low_resource_service', '_local_route_timeout_seconds', '_local_container_timeout_seconds', '_wait_for_local_container_healthy', '_wait_for_local_route_ready', '_link_ecosystem'],
    'tasks_deploy_remote': ['_handle_remote_deployment_legacy', '_remote_failure_message', '_stop_local_service_container', '_remote_deploy_failed', '_handle_remote_deployment', '_resume_remote_deployment', '_copy_remote_deployment_fields', '_poll_remote_deployment', '_is_traefik_not_ready', '_route_misroute_reason', 'self_heal_remote_deployment'],
    'tasks_build': ['_build_function', '_build_uploaded_source', '_resolve_upload_zip_path', '_safe_extract_zip'],
    'tasks_ai_router': ['_escalate_to_ai', '_detect_safe_ollama_ram_mb', '_detect_safe_ollama_cpu', '_ensure_shared_ollama_cpp', '_pull_ollama_models_into_shared', '_cleanup_shared_ollama_if_unused'],
    'tasks_templates': ['one_click_deploy_template_task'],
    'tasks_addons': ['provision_addon_task', 'deprovision_addon_task', 'backup_addon_task', 'restore_addon_task', 'delete_addon_task'],
    'tasks_backup': ['create_service_backup_task', 'create_server_backup_task', 'restore_service_backup_task', 'restore_server_backup_task', 'purge_user_backups_task', 'cleanup_old_backups_task', 'run_scheduled_backups_task'],
    'tasks_transfer': ['execute_server_transfer_task', 'rollback_transfer_task'],
    'tasks_platform_update': ['platform_update_task', 'platform_rollback_task', '_clear_directory_contents'],
    'tasks_maintenance': ['_extract_addon_id_from_name', '_is_stale_maintenance_container', '_clear_orphaned_runtime_resources', 'run_maintenance_task', 'ThrottledLogAppender', 'registry_garbage_collection_task'],
    'tasks_server_update': ['update_remote_server_task', '_redact_remote_update_log', '_append_remote_update_log', '_remote_update_preflight_script', '_remote_update_postflight_script', '_run_ssh_command'],
    'tasks_health': ['auto_authenticate_nodes_task', 'check_managed_servers_health_task', 'node_watchdog_task', 'refresh_managed_server_health', 'sync_master_db_to_agents_task'],
    'tasks_caddy': ['_regenerate_caddyfile']
}

with open('backend/apps/deployments/tasks.py', 'r') as f:
    tasks_source = f.read()
tasks_lines = tasks_source.split('\n')
tasks_tree = ast.parse(tasks_source)

tasks_imports = []
for node in tasks_tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        tasks_imports.append(ast.unparse(node))
tasks_imports_str = '\n'.join(tasks_imports)

def get_block(start_str):
    block = []
    in_block = False
    for line in tasks_lines:
        if line.startswith(start_str):
            in_block = True
            block.append(line)
        elif in_block:
            if line.startswith(')') or line.startswith('}') or line.strip() == '':
                if line.startswith(')') or line.startswith('}'):
                    block.append(line)
                break
            else:
                block.append(line)
    return '\n'.join(block)

ollama_consts = []
for c in ['SHARED_OLLAMA_RAM_FRACTION =', 'SHARED_OLLAMA_MIN_RAM_MB =', 'SHARED_OLLAMA_MAX_RAM_MB =',
          'SHARED_OLLAMA_MIN_CPU_CORES =', 'SHARED_OLLAMA_MAX_CPU_CORES =', 'SHARED_OLLAMA_NAME_PREFIX =', 'SHARED_OLLAMA_PORT =']:
    for line in tasks_lines:
        if line.startswith(c):
            ollama_consts.append(line)
            break

for mod, funcs in tasks_mapping.items():
    out = ["import logging", "logger = logging.getLogger(__name__)", tasks_imports_str, "\n"]
    if mod == 'tasks_ai_router':
        out.extend(ollama_consts)
    if mod == 'tasks_deploy_local':
        out.append(get_block('_SERVICE_DB_MAP = {'))
        out.append(get_block('_SERVICE_URL_PATTERNS = {'))
        out.append(get_block('_PROPAGATED_SECRETS = {'))
        out.append(get_block('_SERVICE_REDIS_DB = {'))
    if mod == 'tasks_server_update':
        out.append(get_block('REMOTE_UPDATE_LOG_LIMIT ='))
    if mod == 'tasks_utils':
        out.append(get_block('AUTO_APPROVE_COMMIT_MARKERS = ('))
    if mod == 'tasks_health':
        out.append("import hashlib\nimport hmac")
    if mod == 'tasks_deploy':
        out.append("from apps.intelligence.models import AIProviderSettings")

    t_code = get_node_source(tasks_tree, tasks_lines, funcs)
    for f in funcs:
        if f in t_code:
            out.append(t_code[f])
            out.append("\n")

    with open(f"backend/apps/deployments/{mod}.py", "w") as f:
        f.write('\n'.join(out))

with open('backend/apps/deployments/tasks.py', 'w') as f:
    f.write('"""Tasks re-export shim"""\n')
    for mod, funcs in tasks_mapping.items():
        f.write(f"from .{mod} import {', '.join(funcs)}\n")
    # inject the missing constants
    f.write("from .tasks_deploy import _IN_PROGRESS_DEPLOYMENT_STATUSES\n")
    f.write("from .tasks_maintenance import MAINTENANCE_ACTIONS\n")

# 3. CROSS IMPORTS
# ----------------
func_to_tasks_mod = {}
for mod, funcs in tasks_mapping.items():
    for f in funcs:
        func_to_tasks_mod[f] = f"from .{mod} import {f}"

func_to_views_mod = {
    '_parse_bool': 'from .views_domains import _parse_bool',
    '_verify_signed_download': 'from .views_files import _verify_signed_download',
    '_open_backup_download_response': 'from .views_files import _open_backup_download_response',
    '_generate_signed_download_url': 'from .views_files import _generate_signed_download_url',
    '_error_response': 'from .views_service import _error_response',
    '_resolve_provider_for_service': 'from .views_service import _resolve_provider_for_service',
    '_has_active_deployment': 'from .views_service import _has_active_deployment',
    '_resolve_provider_for_target': 'from .views_service import _resolve_provider_for_target',
    'ZeroTrustHMACAuthentication': 'from .views_auth import ZeroTrustHMACAuthentication',
    'EmptySerializer': 'from .views_auth import EmptySerializer',
    '_redact_caddyfile_preview': 'from .views_system import _redact_caddyfile_preview',
    '_check_tier_gates_disabled': 'from .views_service import _check_tier_gates_disabled',
    'CaddySecretOrAdminPermission': 'from .views_auth import CaddySecretOrAdminPermission',
    '_normalize_request_domain': 'from .views_domains import _normalize_request_domain',
    '_service_for_domain': 'from .views_domains import _service_for_domain',
}

for file in glob.glob('backend/apps/deployments/tasks_*.py'):
    with open(file, 'r') as f:
        content = f.read()
    new_imports = set()
    for f_name, imp in func_to_tasks_mod.items():
        if re.search(r'\b' + f_name + r'\b', content) and not ('def ' + f_name in content) and not ('class ' + f_name in content):
            new_imports.add(imp)
    if '_IN_PROGRESS_DEPLOYMENT_STATUSES' in content and not '_IN_PROGRESS_DEPLOYMENT_STATUSES =' in content:
        new_imports.add("from .tasks_deploy import _IN_PROGRESS_DEPLOYMENT_STATUSES")
    if 'MAINTENANCE_ACTIONS' in content and not 'MAINTENANCE_ACTIONS =' in content:
        new_imports.add("from .tasks_maintenance import MAINTENANCE_ACTIONS")

    if new_imports:
        idx = content.find('class ') if 'class ' in content else content.find('def ')
        if idx != -1:
            content = content.replace('import logging\nlogger = logging.getLogger(__name__)\n', 'import logging\nlogger = logging.getLogger(__name__)\n' + '\n'.join(new_imports) + '\n')
        with open(file, 'w') as f:
            f.write(content)

for file in glob.glob('backend/apps/deployments/views_*.py'):
    with open(file, 'r') as f:
        content = f.read()
    new_imports = set()
    for f_name, imp in func_to_views_mod.items():
        if re.search(r'\b' + f_name + r'\b', content) and not ('def ' + f_name in content) and not ('class ' + f_name in content):
            new_imports.add(imp)
    if new_imports:
        idx = content.find('class ') if 'class ' in content else content.find('def ')
        if idx != -1:
            content = content.replace('import logging\nlogger = logging.getLogger(__name__)\n', 'import logging\nlogger = logging.getLogger(__name__)\n' + '\n'.join(new_imports) + '\n')
        with open(file, 'w') as f:
            f.write(content)

# Add local fixes
with open('backend/apps/deployments/views_envvars.py', 'r') as f:
    c = f.read()
if 'import re' not in c[:100]:
    with open('backend/apps/deployments/views_envvars.py', 'w') as f:
        f.write('import re\n' + c)

with open('backend/apps/deployments/views_service.py', 'r') as f:
    c = f.read()
c = c.replace("_LOCAL_DEPLOY_TARGET_VALUES = {\n", "_LOCAL_DEPLOY_TARGET_VALUES = {'DOCKER', 'TEMPLATE'}\n_DEPLOY_TARGET_MISSING = object()\n")
c = c.replace("class ServiceViewSet(", "from .tasks import _IN_PROGRESS_DEPLOYMENT_STATUSES\n\nclass ServiceViewSet(")
with open('backend/apps/deployments/views_service.py', 'w') as f:
    f.write(c)

with open('backend/apps/deployments/views_auth.py', 'w') as f:
    f.write('''import logging
from rest_framework import permissions, authentication, exceptions, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework.generics import GenericAPIView
from rest_framework import status
from django.conf import settings
import hmac
import time
import hashlib

logger = logging.getLogger(__name__)

class EmptySerializer(serializers.Serializer):
    pass

class SessionTokenView(GenericAPIView):
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['post', 'options', 'head']

    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        Token.objects.filter(user=user).delete()
        new_token = Token.objects.create(user=user)
        return Response({'token': new_token.key})

class ZeroTrustHMACAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        signature = request.headers.get("X-Gateway-Signature-V2", "")
        timestamp = request.headers.get("X-Request-Timestamp", "")
        nonce = request.headers.get("X-Request-Nonce", "")
        if not signature or not timestamp or not nonce:
            return None
        try:
            req_ts = int(timestamp)
            if abs(int(time.time()) - req_ts) > 60:
                raise authentication.AuthenticationFailed("Timestamp expired")
        except ValueError:
            raise authentication.AuthenticationFailed("Invalid timestamp")
        from django.core.cache import cache
        nonce_key = f"hmac_nonce:{nonce}"
        if cache.get(nonce_key):
            raise authentication.AuthenticationFailed("Nonce already used")
        cache.set(nonce_key, "1", timeout=120)
        gw_secret = getattr(settings, "GATEWAY_SECRET", settings.SECRET_KEY)
        method = request.method
        path = request.get_full_path()
        try:
            body = request.body
        except Exception:
            body = b""
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"
        expected = hmac.new(gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise authentication.AuthenticationFailed("Invalid HMAC signature")
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            raise authentication.AuthenticationFailed("No admin user available")
        return (admin, None)

class CaddySecretOrAdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        expected = self._get_expected_secret()
        if expected:
            provided = request.query_params.get("secret", "")
            if provided and hmac.compare_digest(provided, expected):
                return True
            header_provided = request.headers.get("X-Caddy-Secret", "")
            if header_provided and hmac.compare_digest(header_provided, expected):
                return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and (
            getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)
        ):
            return True
        if not expected:
            return True
        return False

    @staticmethod
    def _get_expected_secret():
        try:
            from .models_core import PlatformConfig
            cfg = PlatformConfig.load()
            db_secret = str(getattr(cfg, 'caddy_ask_secret', '') or '').strip()
            if db_secret:
                return db_secret
        except Exception:
            pass
        return str(getattr(settings, "CADDY_ASK_SECRET", "") or "")
''')
