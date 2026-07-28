"""file browser mixin."""
import contextlib
import logging
import os
import posixpath

from django.db.models import Q
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.teams.permissions import assert_can_write

from ...utils import resolve_running_container, validate_and_sanitize_path

logger = logging.getLogger(__name__)


from ...services.utils_file_browser import exec_file_list


class FileBrowserActionsMixin:
    """FileBrowserActions actions for the viewset."""


    def _resolve_target_type(self, service, latest_deploy):
        """Resolve execution target (remote/lite_agent/local) with fallback."""
        try:
            from apps.deployments.utils.target import resolve_active_execution_target
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
            with contextlib.suppress(Exception):
                path = validate_and_sanitize_path(path, skip_system_check=True, container=container)

        return local_action(container, path) if path is not None else local_action(container)


    @action(detail=True, methods=['get'], url_path='file-browse')
    def file_browse(self, request, pk=None):
        """List files inside the running container (Docker, K8s, or remote node)."""
        service = self.get_object()
        path = request.query_params.get('path', '/')

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            # Stop retrying if the error indicates the node is down or unreachable
            if resp is None or resp.status_code >= 500:
                return resp

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
                except Exception as exc:
                    logger.debug("File browse fallback failed for %s: %s", fb, exc)
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
            local_action=lambda container, path=None: exec_file_list(container, path or '/', fallback_to_root=True),
            path=path,
        )


    def _resolve_remote_server(self, service, latest_deploy):
        """
        Fallback: resolve remote server when active_target_type is not set.
        Checks deployment's target_server, service.server, then provider.
        """
        from apps.deployments.models.core import ManagedServer
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


    @action(detail=True, methods=['get'], url_path='file-download')
    def file_download(self, request, pk=None):
        """Download a file from the container."""
        service = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            bits, _stat = container.get_archive(path)
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
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            import io
            import tarfile
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

            from ..upload_security import validate_upload_size
            size_err = validate_upload_size(uploaded_file, max_size=50 * 1024 * 1024)
            if size_err:
                return size_err

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
            path = validate_and_sanitize_path(path, skip_system_check=True)
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
            import io
            import tarfile
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
