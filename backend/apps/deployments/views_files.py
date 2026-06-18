import logging
logger = logging.getLogger(__name__)
_BACKUP_DOWNLOAD_BLOCK_SIZE = 1024 * 1024
_BACKUP_DOWNLOAD_CONTENT_TYPE = "application/gzip"
_BACKUP_DOWNLOAD_CONTENT_TYPE = "application/gzip"


import os
import posixpath
import hmac
import re
from rest_framework import viewsets, permissions, status, parsers, serializers, authentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.db.models import Prefetch
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction, models
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from django.utils.http import content_disposition_header
from django.core import signing
from apps.deployments.services.github_webhooks import setup_github_webhook
from apps.deployments.services.gitlab_webhooks import setup_gitlab_webhook
from apps.deployments.services.bitbucket_webhooks import setup_bitbucket_webhook
import threading
from .ai_router import DEFAULT_AI_ROUTER_API_BASE, DEFAULT_AI_ROUTER_UI_BASE, DEFAULT_BRAID_ALIAS, is_ai_router_service, persist_ai_router_config, serialize_ai_router_config
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import ServiceSerializer, DeploymentSerializer, DeploymentTriggerSerializer, EnvVarSerializer, DeploymentTimelineSerializer, InstantRollbackSerializer, AuditLogSerializer, DeploymentApproveSerializer, ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .tasks import smart_deploy_task, resume_deploy_task, create_service_backup_task, create_server_backup_task, restore_service_backup_task, enqueue_smart_deploy_task
from .rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from .domain_utils import normalize_domain
from .services.server_guard import ServerGuard
from apps.cloud.models import CloudProvider
import uuid
import logging
import re
from celery.result import AsyncResult
from apps.cloud.docker_client import get_docker_client
from .utils import validate_and_sanitize_path
from apps.deployments.utils import resolve_running_container
from apps.teams.permissions import get_team_q_filter, assert_can_write, assert_can_delete, user_can_read
from .views_audit import AuditLogViewSet
from .views_auth import SessionTokenView
from .views_route_status import RouteStatusView
from .views_transfer import ServerTransferViewSet


class CleanupFileResponse(FileResponse):
    """FileResponse that deletes the underlying file when closed."""
    def __init__(self, *args, **kwargs):
        self._file_path = kwargs.pop('file_path', None)
        block_size = kwargs.pop('block_size', None) or kwargs.pop('blksize', None)
        super().__init__(*args, **kwargs)
        self.block_size = block_size or _BACKUP_DOWNLOAD_BLOCK_SIZE

    def close(self):
        super().close()
        if self._file_path and os.path.exists(self._file_path):
            try:
                os.remove(self._file_path)
            except OSError:
                pass
        if self._file_path:
            parent = os.path.dirname(os.path.abspath(self._file_path))
            if parent and os.path.basename(parent).startswith('smsly-decrypted-'):
                try:
                    os.rmdir(parent)
                except OSError:
                    pass


def _backup_download_headers(response, file_size: int, filename: str):
    response['Content-Type'] = _BACKUP_DOWNLOAD_CONTENT_TYPE
    response['Accept-Ranges'] = 'bytes'
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'private, no-store'
    response['Content-Disposition'] = content_disposition_header(True, filename)
    if file_size is not None:
        response['Content-Length'] = str(file_size)
    return response


def _verify_signed_download(signed_value: str, expected_pk: str, max_age: int = 300) -> bool:
    """Verify a signed download token. Returns True if valid and not expired."""
    try:
        payload = signing.TimestampSigner().unsign_object(signed_value, max_age=max_age)
        return str(payload.get('pk')) == str(expected_pk)
    except (signing.BadSignature, signing.SignatureExpired):
        return False


def _generate_signed_download_url(request, obj_pk: str, url_name: str, path_params: dict | None = None) -> str:
    """Generate a signed download URL valid for 5 minutes."""
    import time
    from django.urls import reverse
    payload = {'pk': str(obj_pk), 'ts': int(time.time())}
    signed = signing.TimestampSigner().sign_object(payload)
    from urllib.parse import urlencode
    params = {'signed': signed}
    if path_params:
        params.update(path_params)
    path = reverse(url_name, args=[obj_pk])
    return request.build_absolute_uri(f"{path}?{urlencode(params)}")


def _parse_single_range(range_header: str, file_size: int):
    if not range_header or not range_header.startswith('bytes='):
        return None
    raw_range = range_header.split('=', 1)[1].strip()
    if ',' in raw_range or '-' not in raw_range:
        raise ValueError("Only a single byte range is supported")
    start_raw, end_raw = raw_range.split('-', 1)
    if not start_raw:
        suffix_length = int(end_raw)
        if suffix_length <= 0:
            raise ValueError("Invalid suffix range")
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1
    if start < 0 or end < start or start >= file_size:
        raise ValueError("Requested range is not satisfiable")
    return start, min(end, file_size - 1)


def _file_iterator(file_path: str, start: int = 0, end: int | None = None, cleanup_path: str | None = None):
    try:
        with open(file_path, 'rb') as file_obj:
            file_obj.seek(start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                read_size = _BACKUP_DOWNLOAD_BLOCK_SIZE if remaining is None else min(_BACKUP_DOWNLOAD_BLOCK_SIZE, remaining)
                chunk = file_obj.read(read_size)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
    finally:
        if cleanup_path and os.path.exists(cleanup_path):
            try:
                os.remove(cleanup_path)
            except OSError:
                pass
        if cleanup_path:
            parent = os.path.dirname(os.path.abspath(cleanup_path))
            if parent and os.path.basename(parent).startswith('smsly-decrypted-'):
                try:
                    os.rmdir(parent)
                except OSError:
                    pass


def _open_backup_download_response(request, file_path: str, filename: str, cleanup_path: str | None = None):
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get('Range') or request.META.get('HTTP_RANGE')
    if range_header:
        try:
            start, end = _parse_single_range(range_header, file_size)
        except (TypeError, ValueError):
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{file_size}'
            response['Accept-Ranges'] = 'bytes'
            return response
        response = StreamingHttpResponse(
            _file_iterator(file_path, start, end, cleanup_path),
            status=206,
            content_type=_BACKUP_DOWNLOAD_CONTENT_TYPE,
        )
        response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        response['Content-Length'] = str(end - start + 1)
        return _backup_download_headers(response, None, filename)

    response = CleanupFileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=filename,
        file_path=cleanup_path,
        block_size=_BACKUP_DOWNLOAD_BLOCK_SIZE,
    )
    return _backup_download_headers(response, file_size, filename)


class ServiceFileActionsMixin:
    def _resolve_target_type(self, service, latest_deploy):
        """Resolve execution target (remote/lite_agent/local) with fallback."""
        try:
            from apps.deployments.utils_target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target.get("server_obj")
            target_type = target.get("target_type")
        except Exception:
            active_server = self._resolve_remote_server(service, latest_deploy)
            target_type = "remote" if active_server else "local"
        return target_type, active_server


    def _dispatch_file_operation(self, service, latest_deploy, remote_config, local_action, path=None):
        """
        Dispatch a file operation to a remote node or local Docker container.

        Args:
            service: Service object.
            latest_deploy: Latest active deployment.
            remote_config: dict with:
                method (str), path_suffix (str),
                params (dict, optional), payload (dict, optional),
                timeout (int, optional, default 30),
                on_success (callable(resp)->Response, optional),
                on_error (callable(resp|None)->Response, optional),
                retry (callable(resp, orchestrator, remote_id, config)->Response|None, optional).
            local_action: callable(container, path=None) -> Response.
            path: Optional path string for symlink resolution.

        Returns:
            Response
        """
        target_type, active_server = self._resolve_target_type(service, latest_deploy)
        attempted_remote = target_type in ("remote", "lite_agent") and active_server

        if attempted_remote:
            from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
            orchestrator = RemoteOrchestrator(active_server)
            remote_id = orchestrator._search_remote_service(service, "/api/v1/services/")
            if not remote_id:
                return Response(
                    {'error': f'Service not found on remote node {active_server.name or active_server.host}'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            try:
                resp = orchestrator._request(
                    method=remote_config['method'],
                    path=f"/api/v1/services/{remote_id}/{remote_config['path_suffix']}/",
                    params=remote_config.get('params'),
                    payload=remote_config.get('payload'),
                    timeout=remote_config.get('timeout', 30),
                )
                if resp and resp.status_code == 200:
                    on_success = remote_config.get('on_success')
                    if on_success:
                        return on_success(resp)
                    return Response(resp.json())
                retry_handler = remote_config.get('retry')
                if retry_handler:
                    retry_result = retry_handler(resp, orchestrator, remote_id, remote_config)
                    if retry_result is not None:
                        return retry_result
                on_error = remote_config.get('on_error')
                if on_error:
                    return on_error(resp)
                return Response(
                    {'error': f'Remote node {active_server.name or active_server.host} returned an error',
                     'details': resp.text[:500] if resp else 'Timeout'},
                    status=resp.status_code if resp else status.HTTP_502_BAD_GATEWAY,
                )
            except Exception as e:
                on_error = remote_config.get('on_error')
                if on_error:
                    return on_error(None)
                return Response(
                    {'error': f'Failed to reach {active_server.name or active_server.host}: {str(e)[:200]}'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # Local execution (only reached when target is local)
        container = resolve_running_container(service, latest_deploy)
        if container is None:
            return Response({'error': 'No running container found'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Symlink resolution for Docker containers
        if path is not None:
            try:
                path = validate_and_sanitize_path(path, container=container)
            except Exception:
                pass

        return local_action(container, path) if path is not None else local_action(container)


    @action(detail=True, methods=['get'], url_path='file-browse')
    def file_browse(self, request, pk=None):
        """List files inside the running container (Docker, K8s, or remote node)."""
        service = self.get_object()
        path = request.query_params.get('path', '/')

        try:
            path = validate_and_sanitize_path(path)
        except Exception as e:
            logger.warning("file_browse 400: Path validation failed for %s: %s", path, str(e))
            return Response({
                'error': 'Path validation failed',
                'details': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            logger.warning("file_browse 400: No active deployment for %s", service.id)
            return Response({
                'error': 'No active deployment',
                'details': f'Deployment {service.id} has no active deployments'
            }, status=status.HTTP_400_BAD_REQUEST)

        def _retry_browse(resp, orchestrator, remote_id, config):
            """Retry file_browse with fallback paths."""
            original_path = config.get('params', {}).get('path', '')
            fallback_paths = ['/app', '/', '/var/www', '/opt', '/home']
            tried = {original_path}
            for fb in fallback_paths:
                if fb in tried:
                    continue
                tried.add(fb)
                logger.warning(
                    f"Remote file_browse failed for path {original_path}, "
                    f"trying fallback: {fb}."
                )
                try:
                    fb_resp = orchestrator._request(
                        method='GET',
                        path=f"/api/v1/services/{remote_id}/file-browse/",
                        params={'path': fb},
                        timeout=10,
                    )
                    if fb_resp and fb_resp.status_code == 200:
                        data = fb_resp.json()
                        data['path'] = fb
                        return Response(data)
                except Exception:
                    pass
            return None

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'GET',
                'path_suffix': 'file-browse',
                'params': {'path': path},
                'timeout': 30,
                'retry': _retry_browse,
            },
            local_action=lambda container, path=None: self._exec_file_list(container, path or '/'),
            path=path,
        )


    def _k8s_file_browse(self, container_id: str, path: str):
        raise NotImplementedError("Kubernetes deployment is not supported. Use Docker or a lite agent.")


    def _k8s_exec_file_op(self, container_id: str, command_args: list):
        raise NotImplementedError("Kubernetes deployment is not supported. Use Docker or a lite agent.")


    def _exec_file_list(self, container, path: str):
        """List files via Docker exec with fallback chain for containers missing coreutils."""
        try:
            cmd_chain = [
                ["ls", "-la", "--time-style=long-iso", path],
                ["ls", "-la", path],
                # Python-based fallback for distroless/minimal images without ls
                ["python3", "-c", (
                    "import os,stat,datetime,sys\n"
                    "p=sys.argv[1]\n"
                    "for f in os.listdir(p):\n"
                    " fp=os.path.join(p,f)\n"
                    " s=os.lstat(fp)\n"
                    " mt=datetime.datetime.fromtimestamp(s.st_mtime).strftime('%Y-%m-%d %H:%M')\n"
                    " print(stat.filemode(s.st_mode),s.st_nlink,s.st_uid,s.st_gid,s.st_size,mt,f)"
                ), path],
            ]
            exit_code = 1
            output = b""
            for cmd in cmd_chain:
                exit_code, output = container.exec_run(cmd)
                if exit_code == 0:
                    break

            if exit_code != 0:
                fallback_path = '/' if path == '/app' else ('/app' if path == '/' else None)
                if fallback_path:
                    path = fallback_path
                    for cmd in cmd_chain:
                        exit_code, output = container.exec_run(cmd)
                        if exit_code == 0:
                            break

            if exit_code != 0:
                logger.warning("_exec_file_list 400: ls command failed. Code: %s, Output: %s", exit_code, output.decode('utf-8', errors='replace'))
                return Response({'error': 'Failed to list directory', 'details': output.decode('utf-8', errors='replace')}, status=status.HTTP_400_BAD_REQUEST)
            files = self._parse_ls_output(output.decode('utf-8', errors='replace'))
            return Response({'path': path, 'files': files})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    def _resolve_remote_server(self, service, latest_deploy):
        """
        Fallback: resolve remote server when active_target_type is not set.
        Checks deployment's target_server, service.server, then provider.
        """
        from apps.deployments.models_core import ManagedServer
        # 1. Check deployment's target_server FK
        if latest_deploy and latest_deploy.target_server_id:
            target = latest_deploy.target_server
            if not target.is_primary:
                return target
        # 2. Check service.server FK
        server = getattr(service, 'server', None)
        if server and not server.is_primary:
            return server
        # 3. Check if service has a remote provider
        provider = getattr(service, 'provider', None)
        if provider and provider.provider_type in ('REMOTE', 'LITE_AGENT'):
            host = provider.host or getattr(provider, 'api_url', None)
            if host:
                return ManagedServer.objects.filter(
                    Q(host=host) | Q(private_ip=host)
                ).first()
        return None


    def _parse_ls_output(self, output: str) -> list:
        """Parse `ls -la` output into file dicts. Supports standard and long-iso time styles."""
        import re
        files = []
        lines = output.splitlines()
        if lines and lines[0].startswith('total'):
            lines = lines[1:]
        for line in lines:
            parts = line.split()
            if not parts:
                continue

            # Detect if time-style=long-iso (e.g. 2026-05-24)
            if len(parts) >= 8 and re.match(r'\d{4}-\d{2}-\d{2}', parts[5]):
                date = f"{parts[5]} {parts[6]}"
                name = " ".join(parts[7:])
            elif len(parts) >= 9:
                # Standard ls -la output: Month Day Time
                date = f"{parts[5]} {parts[6]} {parts[7]}"
                name = " ".join(parts[8:])
            else:
                continue

            files.append({
                'permissions': parts[0],
                'user': parts[2],
                'size': parts[4],
                'date': date,
                'name': name,
            })
        return files


    @action(detail=True, methods=['get'], url_path='file-download')
    def file_download(self, request, pk=None):
        """Download a file from the container."""
        service = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'GET',
                'path_suffix': 'file-download',
                'params': {'path': path},
                'timeout': 30,
                'on_success': lambda resp: StreamingHttpResponse(
                    resp.iter_content(chunk_size=8192),
                    content_type=resp.headers.get('Content-Type', 'application/x-tar'),
                ),
                'on_error': lambda resp: Response(
                    {'error': 'Failed to download from remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_download(container, path),
            path=path,
        )


    def _local_file_download(self, container, path: str):
        try:
            bits, stat = container.get_archive(path)
            response = StreamingHttpResponse(bits, content_type='application/x-tar')
            filename = os.path.basename(path) + ".tar"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=True, methods=['post'], url_path='file-delete')
    def file_delete(self, request, pk=None):
        """Delete a file or directory in the container."""
        service = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-delete',
                'payload': {'path': path},
                'timeout': 15,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to delete on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_delete(container, path),
            path=path,
        )


    def _local_file_delete(self, container, path: str):
        try:
            exit_code, output = container.exec_run(["rm", "-rf", path])
            if exit_code != 0:
                return Response({'error': 'Delete failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Deleted successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=True, methods=['post'], url_path='file-mkdir')
    def file_mkdir(self, request, pk=None):
        """Create a directory in the container."""
        service = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-mkdir',
                'payload': {'path': path},
                'timeout': 15,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to mkdir on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_mkdir(container, path),
            path=path,
        )


    def _local_file_mkdir(self, container, path: str):
        try:
            exit_code, output = container.exec_run(["mkdir", "-p", path])
            if exit_code != 0:
                return Response({'error': 'Mkdir failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Created successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=True, methods=['get'], url_path='file-read')
    def file_read(self, request, pk=None):
        """Read a file's contents from the running container."""
        service = self.get_object()
        path = request.query_params.get('path')

        if not path:
            return Response({'error': 'Path parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'GET',
                'path_suffix': 'file-read',
                'params': {'path': path},
                'timeout': 15,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to read file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_read(container, path),
            path=path,
        )


    def _local_file_read(self, container, path: str):
        try:
            exit_code, output = container.exec_run(["cat", path])
            if exit_code != 0:
                return Response({'error': 'Failed to read file', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)

            from django.conf import settings
            max_read_size = settings.SMSLY_MAX_FILE_READ_SIZE
            if len(output) > max_read_size:
                return Response({'error': 'File too large to read. Use download instead.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

            return Response({'path': path, 'content': output.decode('utf-8')})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=True, methods=['post'], url_path='file-write')
    def file_write(self, request, pk=None):
        """Write contents to a file in the running container."""
        service = self.get_object()
        assert_can_write(self.request.user, service)
        path = request.data.get('path')
        content = request.data.get('content')

        if not path or content is None:
            return Response({'error': 'Path and content parameters are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-write',
                'payload': {'path': path, 'content': content},
                'timeout': 30,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to write file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_write(container, path, content),
            path=path,
        )


    def _local_file_write(self, container, path: str, content: str):
        try:
            import tarfile
            import io
            import time

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                file_data = content.encode('utf-8')
                tarinfo = tarfile.TarInfo(name=os.path.basename(path))
                tarinfo.size = len(file_data)
                tarinfo.mtime = int(time.time())
                tar.addfile(tarinfo, io.BytesIO(file_data))

            tar_stream.seek(0)
            dir_name = os.path.dirname(path)
            exit_code, output = container.exec_run(["mkdir", "-p", dir_name])
            if exit_code != 0:
                return Response({'error': 'Failed to create parent directory', 'details': output.decode()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            success = container.put_archive(dir_name, tar_stream)

            if not success:
                return Response({'error': 'Failed to write file via put_archive'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({'message': 'File written successfully', 'path': path})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=True, methods=['post'], url_path='file-upload')
    def file_upload(self, request, pk=None):
        """Upload a file to the running container."""
        import base64
        service = self.get_object()
        assert_can_write(self.request.user, service)
        path = request.data.get('path')

        if 'file' in request.FILES:
            uploaded_file = request.FILES['file']
            file_bytes = uploaded_file.read()
        elif 'content' in request.data:
            file_bytes = base64.b64decode(request.data['content'])
        else:
            return Response({'error': 'Path and file are required'}, status=status.HTTP_400_BAD_REQUEST)

        if not path:
            return Response({'error': 'Path is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resolved = posixpath.normpath(path)
        except Exception:
            return Response({'error': 'Invalid path'}, status=status.HTTP_400_BAD_REQUEST)

        if hasattr(service, 'volumes'):
            try:
                volumes = list(service.volumes.all())
            except Exception:
                volumes = []
            if volumes:
                mount_paths = [posixpath.normpath(v.mount_path).rstrip('/') or '/' for v in volumes]
                in_mount = False
                for mount in mount_paths:
                    if resolved == mount or resolved.startswith(mount + '/'):
                        try:
                            if posixpath.commonpath([resolved, mount]) == mount:
                                in_mount = True
                                break
                        except ValueError:
                            continue
                if not in_mount:
                    return Response({'error': 'Path traversal blocked'}, status=status.HTTP_400_BAD_REQUEST)
                path = resolved

        try:
            path = validate_and_sanitize_path(path, skip_system_check=False)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        base_name = posixpath.basename(path)
        if not base_name or base_name.startswith('/') or '..' in base_name.split('/'):
            return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-upload',
                'payload': {'path': path, 'content': base64.b64encode(file_bytes).decode('ascii')},
                'timeout': 60,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to upload file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_upload(container, path, file_bytes),
            path=path,
        )


    def _local_file_upload(self, container, path: str, file_bytes: bytes):
        try:
            import tarfile
            import io
            import time

            base_name = posixpath.basename(path)
            if not base_name or base_name.startswith('/') or '..' in base_name.split('/'):
                return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                tarinfo = tarfile.TarInfo(name=base_name)
                tarinfo.size = len(file_bytes)
                tarinfo.mtime = int(time.time())
                tar.addfile(tarinfo, io.BytesIO(file_bytes))

            tar_stream.seek(0)
            dir_name = posixpath.dirname(path)
            exit_code, output = container.exec_run(["mkdir", "-p", dir_name])
            if exit_code != 0:
                return Response({'error': 'Failed to create parent directory', 'details': output.decode()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            success = container.put_archive(dir_name, tar_stream)

            if not success:
                return Response({'error': 'Failed to upload file via put_archive'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({'message': 'File uploaded successfully', 'path': path})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
