"""Real-time build log streaming consumer."""
import asyncio
import contextlib
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .base import authenticate_ws_token, get_websocket_subprotocol, verify_deployment_ownership, logger


def _resolve_buildlog_relay_target(deployment_id: str):
    """Return ``(remote_deployment_id, server)`` for node-executed deploys.

    Same resolution as the terminal relay: the master's row carries
    ``remote_deployment_id`` once delegated, and
    ``resolve_active_execution_target`` identifies the node. Returns
    ``(None, None)`` for local deploys or anything unresolvable
    (caller falls back to the local channel group).
    """
    from apps.deployments.models import Deployment
    try:
        dep = Deployment.objects.select_related('service').get(id=deployment_id)
    except Exception:
        return None, None
    remote_dep_id = (getattr(dep, "remote_deployment_id", "") or "").strip()
    if not remote_dep_id:
        return None, None
    try:
        from apps.deployments.utils.target import resolve_active_execution_target
        target = resolve_active_execution_target(dep.service)
        if target.get("target_type") in ("remote", "lite_agent"):
            server = target.get("server_obj")
            if server is not None:
                return remote_dep_id, server
    except Exception as exc:
        logger.debug("Build-log relay target resolve failed: %s", exc)
    return None, None


class BuildLogConsumer(AsyncWebsocketConsumer):
    """
    Real-time build log streaming consumer.

    Connects to a channel group per deployment and streams build log
    updates as they happen. The Celery task sends logs via channel_layer.

    Usage:
        ws://host/ws/build-logs/{deployment_id}/?token=xxx

    Messages sent to client:
        {
            "type": "build_log",
            "log": "Building image...\n",
            "status": "BUILDING",
            "timestamp": "2026-02-09T17:00:00Z"
        }
        {
            "type": "status_change",
            "status": "ACTIVE",
            "finished_at": "2026-02-09T17:05:00Z",
            "duration_seconds": 300
        }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployment_id = None
        self.group_name = None
        self.user = None
        self._remote_ws = None
        self._remote_relay_task = None

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']

        try:
            self.user = self.scope.get('user')

            if not self.user or not getattr(self.user, 'is_authenticated', False):
                await self.send(text_data=json.dumps({'error': 'Missing or invalid token'}))
                await self.close(code=4001)
                return

            if not await self._verify_ownership():
                await self.send(text_data=json.dumps({'error': 'Access denied'}))
                await self.close(code=4003)
                return

            await self.accept(subprotocol=get_websocket_subprotocol(self.scope))

            # Node-executed deploys stream live from the node (same relay
            # pattern as the terminal consumer). When no relay target
            # resolves — or the relay fails — fall back to the local
            # channel group (mirrored fields copied on poll).
            relayed = await self._maybe_start_remote_relay()
            if not relayed:
                self.group_name = f"build_logs_{self.deployment_id}"
                await self.channel_layer.group_add(
                    self.group_name,
                    self.channel_name
                )

            initial = await self._get_current_state()
            await self.send(text_data=json.dumps({
                'type': 'initial_state',
                **initial
            }))
        except Exception as e:
            if settings.DEBUG:
                logger.error("BuildLogConsumer.connect() failed: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def disconnect(self, code):
        if self._remote_relay_task is not None:
            with contextlib.suppress(Exception):
                self._remote_relay_task.cancel()
            self._remote_relay_task = None
        if self._remote_ws is not None:
            with contextlib.suppress(Exception):
                await self._remote_ws.close()
            self._remote_ws = None
        if self.group_name:
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def _maybe_start_remote_relay(self) -> bool:
        """Open a live relay to the node's build-log stream if remote."""
        try:
            from asgiref.sync import sync_to_async
            remote_dep_id, server = await sync_to_async(
                _resolve_buildlog_relay_target)(self.deployment_id)
        except Exception as exc:
            logger.debug("Build-log relay resolve failed: %s", exc)
            return False
        if not remote_dep_id or server is None:
            return False
        try:
            from urllib.parse import urlparse
            from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
            orchestrator = RemoteOrchestrator(server)
            node_base = ""
            try:
                candidates = orchestrator.node_ws_bases() or []
            except Exception:
                candidates = []
            for candidate in candidates:
                try:
                    cparsed = urlparse(candidate)
                    if cparsed.hostname:
                        scheme = "wss" if cparsed.scheme == "https" else "ws"
                        node_base = f"{scheme}://{cparsed.netloc}"
                        break
                except Exception:
                    continue
            if not node_base:
                base = (getattr(server, "api_url", "") or f"http://{server.host}").rstrip('/')
                parsed = urlparse(base)
                scheme = "wss" if parsed.scheme == "https" else "ws"
                node_base = f"{scheme}://{parsed.netloc}" if parsed.netloc else ""
            if not node_base:
                return False
            headers = orchestrator.ws_auth_headers(remote_dep_id)
            token_value = headers.get("Authorization", "").replace("Token ", "").replace("Bearer ", "")
            extra = {k: v for k, v in dict(headers).items() if v}
            import websockets
            ws_url = f"{node_base}/ws/build-logs/{remote_dep_id}/"
            connect_kwargs = dict(
                subprotocols=["token", token_value] if token_value else ["token"],
                max_size=4 * 1024 * 1024,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
            )
            try:
                self._remote_ws = await websockets.connect(
                    ws_url, additional_headers=[(k, v) for k, v in extra.items()], **connect_kwargs,
                )
            except TypeError:
                # websockets>=15 renamed additional_headers -> extra_headers
                self._remote_ws = await websockets.connect(
                    ws_url, extra_headers=[(k, v) for k, v in extra.items()], **connect_kwargs,
                )
        except Exception as exc:
            logger.debug("Build-log remote WS connect failed: %s", exc)
            self._remote_ws = None
            return False
        self._remote_relay_task = asyncio.create_task(self._relay_remote_output())
        return True

    async def _relay_remote_output(self):
        ws = self._remote_ws
        if ws is None:
            return
        try:
            async for message in ws:
                try:
                    payload = json.loads(message) if isinstance(message, str) else {"log": str(message)}
                except Exception:
                    payload = {"log": str(message)}
                if not isinstance(payload, dict):
                    payload = {"log": str(payload)}
                await self.send(text_data=json.dumps({
                    "type": payload.get("type", "build_log"),
                    "log": payload.get("log", ""),
                    "status": payload.get("status", ""),
                    "timestamp": payload.get("timestamp", ""),
                    "relayed": True,
                }))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Build-log relay ended: %s", exc)
        finally:
            self._remote_ws = None

    async def receive(self, text_data=None, bytes_data=None):
        if not await self._revalidate_auth():
            await self.close(code=4001)
            return

    async def _revalidate_auth(self) -> bool:
        if not self.user or not self.deployment_id:
            return False
        return await self._verify_ownership()

    async def build_log(self, event):
        await self.send(text_data=json.dumps({
            'type': 'build_log',
            'log': event['log'],
            'status': event.get('status', ''),
            'timestamp': event.get('timestamp', ''),
        }))

    async def status_change(self, event):
        await self.send(text_data=json.dumps({
            'type': 'status_change',
            'status': event['status'],
            'finished_at': event.get('finished_at', ''),
            'duration_seconds': event.get('duration_seconds'),
        }))

    async def pipeline_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'pipeline_update',
            'stages': event.get('stages', []),
        }))

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)

    async def _verify_ownership(self):
        return await verify_deployment_ownership(self.user, self.deployment_id)

    @database_sync_to_async
    def _get_current_state(self):
        from apps.deployments.models import Deployment
        try:
            d = Deployment.objects.get(id=self.deployment_id)
            safe_logs = (d.build_logs or "").replace('\x00', '')
            return {
                'build_logs': safe_logs,
                'status': d.status,
                'started_at': d.started_at.isoformat() if d.started_at else None,
                'finished_at': d.finished_at.isoformat() if d.finished_at else None,
                'duration_seconds': d.duration_seconds,
                'commit_hash': d.commit_hash,
                'commit_message': d.commit_message,
            }
        except Deployment.DoesNotExist:
            return {'error': 'Deployment not found'}
