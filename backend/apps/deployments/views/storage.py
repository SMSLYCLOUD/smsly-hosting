import base64
import logging
import os
import posixpath
import re
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import StreamingHttpResponse
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from apps.deployments.utils.file_browser import exec_file_list

from ..models import Service  # type: ignore[attr-defined]
from ..models.storage import Volume
from ..utils import resolve_running_container, validate_and_sanitize_path

logger = logging.getLogger(__name__)


# SECURITY: paths that, if mounted into a tenant container, grant
# host access (docker socket, /etc, /proc, /sys) or are simply
# system directories that should never appear as a volume mount.
# Allow-list of acceptable mount-path roots for tenant volumes.
_VOLUME_ALLOWED_ROOTS = (
    "/data", "/var/lib/smsly", "/srv", "/opt/app", "/workspace",
    "/home/smsly", "/mnt", "/storage",
)
_VOLUME_FORBIDDEN_PATHS = (
    "/var/run/docker.sock",
    "/etc",
    "/etc/",
    "/proc",
    "/proc/",
    "/sys",
    "/sys/",
    "/dev",
    "/dev/",
    "/",
    "/root",
    "/root/",
    "/boot",
    "/boot/",
    "/var/run",
    "/var/run/",
    "/var/log",
    "/var/log/",
)
_VOLUME_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_VOLUME_MOUNT_PATH_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_./-]*$")


def _validate_volume_mount_path(value: str) -> str:
    """Reject any path that is not a normal POSIX path under an allow-listed
    root, and reject system paths (/etc, /proc, /var/run/docker.sock, …)
    that would grant the container host privileges.

    Returns the cleaned path; raises ``serializers.ValidationError`` on
    rejection so DRF surfaces a 400 to the caller.
    """
    if not isinstance(value, str) or not value:
        raise serializers.ValidationError(
            {"mount_path": "mount_path is required and must be a string."}
        )
    # Must start with /
    if not value.startswith("/"):
        raise serializers.ValidationError(
            {"mount_path": "mount_path must be an absolute path (start with /)."}
        )
    # Normalise: collapse double slashes, drop trailing slash
    normalised = posixpath.normpath(value)
    # Reject forbidden paths (exact match or as a directory prefix)
    for forbidden in _VOLUME_FORBIDDEN_PATHS:
        if forbidden == "/":
            # Only reject the literal root, not every path that starts
            # with "/" (which is every absolute path).
            if normalised == "/":
                raise serializers.ValidationError(
                    {"mount_path": "mount_path '/' is on the platform blocklist."}
                )
            continue
        normalised_stripped = forbidden.rstrip("/")
        if normalised == normalised_stripped:
            raise serializers.ValidationError(
                {"mount_path": f"mount_path {normalised!r} is on the platform blocklist."}
            )
        if forbidden.endswith("/") and normalised.startswith(forbidden):
            raise serializers.ValidationError(
                {"mount_path": f"mount_path {normalised!r} is on the platform blocklist."}
            )
    # Reject if it contains .. (defence-in-depth even though normpath collapses it)
    if ".." in normalised.split("/"):
        raise serializers.ValidationError(
            {"mount_path": "mount_path must not contain '..'."}
        )
    # Must be under an allow-listed root
    if not any(normalised == root or normalised.startswith(root + "/")
               for root in _VOLUME_ALLOWED_ROOTS):
        raise serializers.ValidationError(
            {"mount_path":
                f"mount_path must start with one of "
                f"{', '.join(_VOLUME_ALLOWED_ROOTS)}."}
        )
    if not _VOLUME_MOUNT_PATH_RE.match(normalised):
        raise serializers.ValidationError(
            {"mount_path": "mount_path contains invalid characters."}
        )
    return normalised


def _validate_volume_name(value: str) -> str:
    """Volume.name becomes a real ``docker volume create <name>`` and
    is reused as a host bind key — restrict to a docker-safe slug."""
    if not isinstance(value, str) or not value:
        raise serializers.ValidationError(
            {"name": "name is required and must be a string."}
        )
    if not _VOLUME_NAME_RE.match(value):
        raise serializers.ValidationError(
            {"name": "name must be lowercase alphanumeric with '.', '_', or '-', "
                     "starting with a letter or digit (max 63 chars)."}
        )
    # Block names that could collide with platform internals
    forbidden_prefixes = ("smsly-", "smsly_", "platform-", "pgcat-", "caddy-",
                          "redis-", "postgres-", "rabbitmq-", "traefik-",
                          "smsly-system-", "docker-")
    if any(value.startswith(p) for p in forbidden_prefixes):
        raise serializers.ValidationError(
            {"name": f"name {value!r} is on the platform blocklist."}
        )
    return value


class VolumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Volume
        fields = '__all__'
        read_only_fields = ('service',)

    def validate_mount_path(self, value):
        return _validate_volume_mount_path(value)

    def validate_name(self, value):
        return _validate_volume_name(value)

class VolumeViewSet(viewsets.ModelViewSet):
    serializer_class = VolumeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _validated_service_uuid(self):
        service_pk = self.kwargs.get('service_pk')
        if not service_pk:
            return None
        try:
            return uuid.UUID(str(service_pk))
        except (ValueError, TypeError, AttributeError):
            return None

    def get_queryset(self):
        service_uuid = self._validated_service_uuid()
        if service_uuid:
            return Volume.objects.filter(
                service_id=service_uuid,
                service__owner=self.request.user,
            ).order_by("id")
        return Volume.objects.filter(
            service__owner=self.request.user,
        ).order_by("id")

    def perform_create(self, serializer):
        service_uuid = self._validated_service_uuid()
        if not service_uuid:
            raise NotFound("Service not found.")
        try:
            service = Service.objects.get(pk=service_uuid)
        except (Service.DoesNotExist, DjangoValidationError):
            raise NotFound("Service not found.")
        # M-1 fix: verify the requesting user owns this service
        if service.owner != self.request.user:
            raise PermissionDenied("You do not own this service.")
        serializer.save(service=service)

    def _resolve_volume_target(self, service):
        """Resolve whether the volume's service runs locally or remotely."""
        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            server = getattr(service, 'server', None)
            if server and not getattr(server, 'is_primary', True):
                return 'lite_agent' if getattr(server, 'is_lite_agent', False) else 'remote', server
            provider = getattr(service, 'provider', None)
            if provider and provider.provider_type in ('REMOTE', 'LITE_AGENT'):
                from apps.deployments.models.core import ManagedServer
                host = getattr(provider, 'host', None) or getattr(provider, 'api_url', None)
                if host:
                    server = ManagedServer.objects.filter(host=host).first()
                    if not server:
                        server = ManagedServer.objects.filter(private_ip=host).first()
                    if server:
                        return 'remote', server
            return 'local', None
        try:
            from apps.deployments.utils.target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target.get("server_obj")
            target_type = target.get("target_type")
        except Exception:
            from apps.deployments.utils.target import resolve_remote_server
            active_server = resolve_remote_server(service, latest_deploy)
            target_type = "remote" if active_server else "local"
        return target_type, active_server

    def _find_remote_volume_id(self, orchestrator, remote_service_id, volume_name):
        """Find a volume's remote ID by its name on the remote service."""
        resp = orchestrator._request(
            method='GET',
            path=f"/api/v1/services/{remote_service_id}/volumes/",
            timeout=15,
        )
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                volumes = data if isinstance(data, list) else data.get('results', data.get('volumes', []))
                for vol in volumes:
                    if isinstance(vol, dict) and vol.get('name') == volume_name:
                        return vol.get('id')
            except Exception:
                pass
        return None

    def _proxied_volume_action(self, volume, config):
        """Proxy a volume file operation to a remote node, resolving IDs dynamically."""
        service = volume.service
        target_type, active_server = self._resolve_volume_target(service)
        if target_type not in ("remote", "lite_agent") or not active_server:
            return 'local', None, None

        from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
        orchestrator = RemoteOrchestrator(active_server)
        remote_service_id = orchestrator._search_remote_service(service, "/api/v1/services/")
        if not remote_service_id:
            raise NotFound("Service not found on remote node")

        remote_volume_id = self._find_remote_volume_id(orchestrator, remote_service_id, volume.name)
        if not remote_volume_id:
            logger.warning(f"Volume '{volume.name}' not found on remote node for service {service.id}")
            raise NotFound("Volume not found on remote node")

        return 'remote', orchestrator, (remote_service_id, remote_volume_id)

    def _exec_remote_volume_request(self, orchestrator, remote_ids, volume, config):
        """Execute the remote volume file operation and return a Response."""
        remote_service_id, remote_volume_id = remote_ids
        path_suffix = config['path_suffix']
        method = config['method']

        url_path = f"/api/v1/services/{remote_service_id}/volumes/{remote_volume_id}/{path_suffix}/"

        try:
            resp = orchestrator._request(
                method=method,
                path=url_path,
                params=config.get('params'),
                payload=config.get('payload'),
                timeout=config.get('timeout', 30),
            )
            if resp and resp.status_code == 200:
                on_success = config.get('on_success')
                if on_success:
                    return on_success(resp)
                return Response(resp.json())
            on_error = config.get('on_error')
            if on_error:
                return on_error(resp)
            return Response(
                {'error': 'Remote node returned an error', 'details': resp.text if resp else 'Timeout'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            if config.get('fallthrough_on_exception'):
                logger.warning(
                    f"Remote volume {path_suffix} failed for {volume.name}, falling back to local: {e}"
                )
                return None
            on_error = config.get('on_error')
            if on_error:
                return on_error(None)
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _volume_dispatch(self, volume, config, local_action, path=None):
        """Dispatch a volume file operation to remote or local."""
        target_type, orchestrator, remote_ids = self._proxied_volume_action(volume, config)
        if target_type == 'remote':
            result = self._exec_remote_volume_request(orchestrator, remote_ids, volume, config)
            if result is not None:
                return result
            if not config.get('fallthrough_on_exception'):
                return Response({'error': 'Remote volume operation failed'}, status=status.HTTP_502_BAD_GATEWAY)

        container = resolve_running_container(volume.service)
        if container is None:
            return Response({'error': 'No running container found'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if path:
            path = validate_and_sanitize_path(path, container=container)
        return local_action(container, path)

    @action(detail=True, methods=['get'])
    def browse(self, request, pk=None, service_pk=None):
        """
        List files in the volume.
        Uses docker exec ls -la on the active container.
        """
        volume = self.get_object()
        path = request.query_params.get('path', volume.mount_path)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)},
                            status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'},
                            status=status.HTTP_403_FORBIDDEN)

        return self._volume_dispatch(
            volume,
            {
                'method': 'GET',
                'path_suffix': 'browse',
                'params': {'path': path},
                'timeout': 30,
                'fallthrough_on_exception': True,
            },
            local_action=lambda container, p: self._local_volume_browse(container, p, mount),
            path=path,
        )

    def _local_volume_browse(self, container, path, mount):
        # Resolve real path inside container to prevent symlink escapes
        exit_code, output = container.exec_run(
            ["readlink", "-f", path], user="root"
        )
        if exit_code != 0:
            # Fallback for distroless
            exit_code, output = container.exec_run(
                ["python3", "-c", "import os,sys; print(os.path.realpath(sys.argv[1]))", path], user="root"
            )

        if exit_code == 0:
            real_path = output.decode('utf-8').strip()
            # If path resolution succeeds but points outside mount, reject.
            if not (real_path == mount or real_path.startswith(mount + "/")):
                return Response({'error': 'Path escapes volume mount'}, status=status.HTTP_403_FORBIDDEN)

        return exec_file_list(container, path, fallback_to_root=False, user="root")

    @action(detail=True, methods=['post'], url_path='delete-file')
    def delete_file(self, request, pk=None, service_pk=None):
        """Delete a file or directory in the volume."""
        volume = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        return self._volume_dispatch(
            volume,
            {
                'method': 'POST',
                'path_suffix': 'delete-file',
                'payload': {'path': path},
                'timeout': 15,
                'fallthrough_on_exception': True,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to delete on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=self._local_volume_delete,
            path=path,
        )

    def _local_volume_delete(self, container, path):
        try:
            exit_code, output = container.exec_run(["rm", "-rf", path], user="root")
            if exit_code != 0:
                return Response({'error': 'Delete failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Deleted successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='download-file')
    def download_file(self, request, pk=None, service_pk=None):
        """Download a file from the volume."""
        volume = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        return self._volume_dispatch(
            volume,
            {
                'method': 'GET',
                'path_suffix': 'download-file',
                'params': {'path': path},
                'timeout': 30,
                'fallthrough_on_exception': True,
                'on_success': lambda resp: StreamingHttpResponse(
                    resp.iter_content(chunk_size=8192),
                    content_type=resp.headers.get('Content-Type', 'application/x-tar'),
                ),
                'on_error': lambda resp: Response(
                    {'error': 'Failed to download from remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=self._local_volume_download,
            path=path,
        )

    def _local_volume_download(self, container, path):
        try:
            bits, _stat = container.get_archive(path)
            response = StreamingHttpResponse(bits, content_type='application/x-tar')
            filename = os.path.basename(path) + ".tar"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='mkdir')
    def mkdir(self, request, pk=None, service_pk=None):
        """Create a directory in the volume."""
        volume = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        return self._volume_dispatch(
            volume,
            {
                'method': 'POST',
                'path_suffix': 'mkdir',
                'payload': {'path': path},
                'timeout': 15,
                'fallthrough_on_exception': True,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to mkdir on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=self._local_volume_mkdir,
            path=path,
        )

    def _local_volume_mkdir(self, container, path):
        try:
            exit_code, output = container.exec_run(["mkdir", "-p", path], user="root")
            if exit_code != 0:
                return Response({'error': 'Mkdir failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Created successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='file-read')
    def file_read(self, request, pk=None, service_pk=None):
        """Read a file's contents from the volume."""
        volume = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        return self._volume_dispatch(
            volume,
            {
                'method': 'GET',
                'path_suffix': 'file-read',
                'params': {'path': path},
                'timeout': 15,
                'fallthrough_on_exception': True,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to read file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=self._local_volume_file_read,
            path=path,
        )

    def _local_volume_file_read(self, container, path):
        try:
            exit_code, output = container.exec_run(["cat", path])
            if exit_code != 0:
                return Response({'error': 'Failed to read file', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)

            from django.conf import settings
            max_read_size = getattr(settings, 'SMSLY_MAX_FILE_READ_SIZE', 10 * 1024 * 1024)
            if len(output) > max_read_size:
                return Response({'error': 'File too large to read. Use download instead.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

            return Response({'path': path, 'content': output.decode('utf-8')})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='file-write')
    def file_write(self, request, pk=None, service_pk=None):
        """Write contents to a file in the volume."""
        volume = self.get_object()
        path = request.data.get('path')
        content = request.data.get('content')

        if not path or content is None:
            return Response({'error': 'Path and content are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path).rstrip('/') or '/'
        resolved = posixpath.normpath(path)
        if not (resolved == mount or resolved.startswith(mount + "/")):
            return Response(
                {'error': f'Path {path} escapes the volume mount {volume.mount_path}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            common = posixpath.commonpath([resolved, mount])
        except ValueError:
            return Response({'error': 'Path traversal blocked'}, status=status.HTTP_400_BAD_REQUEST)
        if common != mount:
            return Response({'error': 'Path traversal blocked'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sanitized = validate_and_sanitize_path(resolved, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        base_name = posixpath.basename(sanitized)
        if not base_name or base_name.startswith('/') or '..' in base_name.split('/'):
            return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)

        path = sanitized

        return self._volume_dispatch(
            volume,
            {
                'method': 'POST',
                'path_suffix': 'file-write',
                'payload': {'path': path, 'content': content},
                'timeout': 30,
                'fallthrough_on_exception': True,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to write file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, p: self._local_volume_file_write(container, p, content),
            path=path,
        )

    def _local_volume_file_write(self, container, path, content):
        try:
            import io
            import tarfile
            import time

            base_name = posixpath.basename(path)
            if not base_name or base_name.startswith('/') or '..' in base_name.split('/'):
                return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)

            # Accept bytes or str; encode str to bytes
            file_data = content.encode('utf-8') if isinstance(content, str) else content

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                tarinfo = tarfile.TarInfo(name=base_name)
                tarinfo.size = len(file_data)
                tarinfo.mtime = int(time.time())
                tar.addfile(tarinfo, io.BytesIO(file_data))

            tar_stream.seek(0)
            dir_name = posixpath.dirname(path)
            exit_code, output = container.exec_run(["mkdir", "-p", dir_name])
            if exit_code != 0:
                return Response({'error': 'Failed to create parent directory', 'details': output.decode()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            success = container.put_archive(dir_name, tar_stream)

            if not success:
                return Response({'error': 'Failed to write file via put_archive'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({'message': 'File written successfully', 'path': path})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='upload')
    def file_upload(self, request, pk=None, service_pk=None):
        """Upload a file to the volume via multipart form data."""
        volume = self.get_object()
        path = request.data.get('path')
        if 'file' not in request.FILES or not path:
            return Response({'error': 'file and path are required'}, status=status.HTTP_400_BAD_REQUEST)

        uploaded_file = request.FILES['file']
        file_bytes = uploaded_file.read()

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except ValueError as e:
            return Response({'error': 'Invalid path', 'details': str(e)}, status=status.HTTP_403_FORBIDDEN)

        mount = posixpath.normpath(volume.mount_path).rstrip('/') or '/'
        resolved = posixpath.normpath(path)
        if not (resolved == mount or resolved.startswith(mount + "/")):
            return Response(
                {'error': f'Path {path} escapes the volume mount {volume.mount_path}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return self._volume_dispatch(
            volume,
            {
                'method': 'POST',
                'path_suffix': 'file-write',
                'payload': {'path': path, 'content': file_bytes.decode('utf-8', errors='replace') if path.lower().endswith(('.txt','.log','.json','.yaml','.yml','.xml','.csv','.md','.env','.ini','.cfg','.conf','.sql','.py','.js','.ts','.html','.css','.sh')) else base64.b64encode(file_bytes).decode('ascii'), 'content_binary': not path.lower().endswith(('.txt','.log','.json','.yaml','.yml','.xml','.csv','.md','.env','.ini','.cfg','.conf','.sql','.py','.js','.ts','.html','.css','.sh'))},
                'timeout': 60,
                'fallthrough_on_exception': True,
            },
            local_action=lambda container, p: self._local_volume_file_write(container, p, file_bytes),
            path=path,
        )
