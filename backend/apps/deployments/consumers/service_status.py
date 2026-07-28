"""WebSocket consumer for real-time service status updates."""
import asyncio
import contextlib
import json
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .base import authenticate_ws_token, _REDIS_WS_ERRORS, logger


class ServiceStatusConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time service status updates.

    Connects to channel groups per user and broadcasts service status changes
    as they happen. Services update their status via channel_layer.

    Usage:
        ws://host/ws/service-status/?token=xxx

    Messages sent to client:
        {
            "type": "service_status_update",
            "service_id": "uuid",
            "service_name": "name",
            "status": "ACTIVE|FAILED|DELETION_PENDING...",
            "deployment_status": "ACTIVE|FAILED|...",
            "updated_at": "2026-05-21T12:00:00Z"
        }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.user_group_name = None
        self._periodic_auth_task = None
        self._heartbeat_task = None
        self.is_disconnected = False
        self._redis_healthy = True

    async def connect(self):
        try:
            self.user = self.scope.get('user')
            if not self.user or not getattr(self.user, 'is_authenticated', False):
                await self.accept()
                await self.send(text_data=json.dumps({'error': 'Authentication required'}))
                await self.close(code=4001)
                return

            self.user_group_name = f"user_services_{self.user.id}"
            await self._join_group_with_retry()
            await self.accept()

            await self._send_initial_services()

            self._periodic_auth_task = asyncio.create_task(self._periodic_auth_check())
            self._heartbeat_task = asyncio.create_task(self._heartbeat())
        except Exception as e:
            if settings.DEBUG:
                logger.error("ServiceStatusConsumer.connect() failed: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def disconnect(self, code):
        self.is_disconnected = True
        if self._periodic_auth_task and not self._periodic_auth_task.done():
            self._periodic_auth_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._periodic_auth_task
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self.user_group_name:
            try:
                await self.channel_layer.group_discard(
                    self.user_group_name,
                    self.channel_name
                )
            except Exception as exc:
                logger.debug("group_discard failed (Redis may be down): %s", exc)

    async def _join_group_with_retry(self, retries=3, delay=1.0):
        for attempt in range(retries):
            try:
                await self.channel_layer.group_add(
                    self.user_group_name,
                    self.channel_name
                )
                self._redis_healthy = True
                return
            except _REDIS_WS_ERRORS as exc:
                self._redis_healthy = False
                if attempt == retries - 1:
                    logger.error(
                        "Redis unavailable after %d retries, closing WS for user %s: %s",
                        retries, getattr(self.user, 'id', '?'), exc,
                    )
                    raise
                logger.warning(
                    "Redis error joining group (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, retries, delay, exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    async def _heartbeat(self):
        try:
            while not self.is_disconnected:
                await asyncio.sleep(30)
                if self.is_disconnected:
                    break
                try:
                    await self.send(text_data=json.dumps({
                        'type': 'heartbeat',
                        'ts': int(time.time()),
                    }))
                except Exception:
                    logger.debug("Heartbeat send failed for user %s, closing", getattr(self.user, 'id', '?'))
                    break
                if self.user_group_name and not self._redis_healthy:
                    try:
                        await self._join_group_with_retry(retries=2, delay=2.0)
                    except _REDIS_WS_ERRORS:
                        logger.warning(
                            "Redis still unavailable for user %s, messages may be lost",
                            getattr(self.user, 'id', '?'),
                        )
        except asyncio.CancelledError:
            pass

    async def receive(self, text_data=None, bytes_data=None):
        if not self.scope.get('user') or not getattr(self.scope.get('user'), 'is_authenticated', False):
            await self.close(code=4001)
            return
        if not await self._revalidate_user():
            await self.close(code=4001)
            return

        if text_data:
            try:
                data = json.loads(text_data)
                if data.get('type') == 'ping':
                    await self.send(text_data=json.dumps({'type': 'pong'}))
            except json.JSONDecodeError:
                pass

    async def _revalidate_user(self) -> bool:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        @database_sync_to_async
        def check_user(user_id):
            try:
                u = User.objects.get(pk=user_id)
                return u.is_active
            except User.DoesNotExist:
                return False

        user = self.scope.get('user')
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        return await check_user(user.pk)

    async def _periodic_auth_check(self):
        try:
            while not self.is_disconnected:
                await asyncio.sleep(300)
                if not await self._revalidate_user():
                    logger.warning(
                        "ServiceStatusConsumer: user %s deactivated, closing WS",
                        getattr(self.scope.get('user'), 'id', '?'),
                    )
                    await self.close(code=4001)
                    break
        except asyncio.CancelledError:
            pass

    async def service_status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'service_status_update',
            'service_id': event['service_id'],
            'service_name': event['service_name'],
            'status': event['status'],
            'deployment_status': event.get('deployment_status', 'unknown'),
            'updated_at': event.get('updated_at', ''),
        }))

    async def deployment_status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'deployment_status_update',
            'service_id': event['service_id'],
            'service_name': event['service_name'],
            'deployment_id': event['deployment_id'],
            'status': event['status'],
            'updated_at': event.get('updated_at', ''),
        }))

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)

    @database_sync_to_async
    def _get_user_services(self):
        from django.db.models import Q
        from apps.deployments.models import Service
        services = Service.objects.filter(
            Q(owner=self.user) | Q(project__team__members__user=self.user)
        ).distinct().prefetch_related(
            'deployments', 'deployments__service'
        )

        services_with_status = []
        for service in services:
            latest_deployment = service.deployments.order_by('-created_at').first()
            services_with_status.append({
                'id': str(service.id),
                'name': service.name,
                'status': service.status,
                'deployment_status': latest_deployment.status if latest_deployment else 'unknown',
                'updated_at': service.updated_at.isoformat() if service.updated_at else None,
            })

        return services_with_status

    async def _send_initial_services(self):
        try:
            services = await self._get_user_services()
            for service in services:
                await self.send(text_data=json.dumps({
                    'type': 'service_status_update',
                    'service_id': service['id'],
                    'service_name': service['name'],
                    'status': service['status'],
                    'deployment_status': service['deployment_status'],
                    'updated_at': service['updated_at'],
                }))
        except Exception as e:
            logger.error("Error sending initial service statuses: %s", e)
