# pylint: disable=logging-fstring-interpolation
"""Server module."""
"""
SMSLY Tunnels - WebSocket Tunnel Server

Expose local development servers to public URLs via WebSocket tunnels.
Similar to ngrok, but integrated with SMSLY Hosting.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from aiohttp import WSMsgType, web
from django.conf import settings

logger = logging.getLogger('smsly.tunnels')


def get_tunnel_base_domain() -> str:
    """Resolve the active tunnel base domain from Django settings."""
    return getattr(settings, 'TUNNEL_BASE_DOMAIN', 'tunnel.localhost')


@dataclass
class TunnelConnection:  # pylint: disable=too-many-instance-attributes
    """Represents an active tunnel connection."""
    tunnel_id: str
    subdomain: str
    websocket: web.WebSocketResponse
    user_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    request_count: int = 0

    def public_url(self, base_domain: str | None = None) -> str:
        """Get public URL."""
        base_domain = base_domain or get_tunnel_base_domain()
        return f"https://{self.subdomain}.{base_domain}"


@dataclass
class RequestLog:  # pylint: disable=too-many-instance-attributes
    """Logged HTTP request for inspection."""
    request_id: str
    tunnel_id: str
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    timestamp: datetime
    response_status: int | None = None
    response_time_ms: int | None = None


class TunnelServer:  # pylint: disable=too-many-instance-attributes
    """
    WebSocket-based tunnel server.

    Routes incoming HTTP requests to connected tunnel clients.
    """

    def __init__(self, base_domain: str | None = None):
        self.base_domain = base_domain or get_tunnel_base_domain()
        self.tunnels: dict[str, TunnelConnection] = {}  # subdomain -> tunnel
        self.request_logs: dict[str, list] = {}  # tunnel_id -> [RequestLog]
        # tunnel_id -> request_id -> Future[dict]
        # Only the WS handler reads from the socket and resolves these futures.
        self.pending_responses: dict[str, dict[str, asyncio.Future]] = {}
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        """Setup HTTP routes."""
        self.app.router.add_get('/ws/tunnel', self.handle_tunnel_connect)
        self.app.router.add_get('/api/tunnels', self.list_tunnels)
        self.app.router.add_get('/api/tunnels/{tunnel_id}/requests', self.get_request_logs)
        self.app.router.add_post('/api/tunnels/{tunnel_id}/replay/{request_id}', self.replay_request)
        # Catch-all for tunneled requests (subdomain routing handled by reverse proxy)
        self.app.router.add_route('*', '/{path:.*}', self.handle_tunneled_request)

    def generate_subdomain(self) -> str:
        """Generate a unique subdomain."""
        return uuid.uuid4().hex[:8]

    @staticmethod
    def _verify_tunnel_token(token: str, tunnel_secret: str) -> dict | None:
        """
        Verify an HMAC-signed tunnel token.

        Returns the decoded payload dict on success, or None if invalid.
        Expected payload: { user_id: str, exp: float }
        """
        if not token or not tunnel_secret:
            return None
        try:
            parts = token.split('.')
            if len(parts) != 2:
                return None
            payload_b64, sig_b64 = parts
            payload_bytes = base64.urlsafe_b64decode(payload_b64 + '==')
            expected_sig = hmac.new(
                tunnel_secret.encode(), payload_bytes, hashlib.sha256
            ).digest()
            actual_sig = base64.urlsafe_b64decode(sig_b64 + '==')
            if not hmac.compare_digest(expected_sig, actual_sig):
                return None
            payload = json.loads(payload_bytes)
            # Check expiry
            if payload.get('exp', 0) < time.time():
                return None
            return payload
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    @staticmethod
    async def _check_reserved_subdomain_owner(subdomain: str, user_id: str) -> bool:
        """Return True if subdomain is unreserved or actively reserved by user_id."""
        from asgiref.sync import sync_to_async
        from apps.deployments.models.tunnels import ReservedSubdomain

        def _check() -> bool:
            reservation = ReservedSubdomain.objects.filter(
                subdomain=subdomain, is_active=True,
            ).first()
            if reservation is None:
                return True
            return bool(user_id) and str(reservation.owner_id) == str(user_id)

        return await sync_to_async(_check, thread_sensitive=True)()

    async def handle_tunnel_connect(self, request: web.Request) -> web.WebSocketResponse:
        """
        Handle new tunnel WebSocket connection from CLI client.

        Client connects and receives assigned subdomain.
        Future HTTP requests to that subdomain are forwarded via WebSocket.
        """
        # --- AUTH: Verify token before upgrading to WebSocket ---
        token = request.query.get('token', '')
        tunnel_secret = str(getattr(settings, 'TUNNEL_SECRET', '') or
                            getattr(settings, 'GATEWAY_SECRET', ''))
        payload = self._verify_tunnel_token(token, tunnel_secret)
        if not payload:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({'error': 'Authentication failed: invalid or expired token'})
            await ws.close()
            return ws

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Get optional custom subdomain from query
        custom_subdomain = (request.query.get('subdomain') or '').strip().lower()
        user_id = str(payload.get('user_id', ''))

        if custom_subdomain:
            if (
                not all(c.isalnum() or c == '-' for c in custom_subdomain)
                or custom_subdomain.startswith('-')
                or custom_subdomain.endswith('-')
                or len(custom_subdomain) > 63
            ):
                await ws.send_json({'error': 'Invalid subdomain format'})
                await ws.close()
                return ws

            from apps.deployments.views.subdomains import RESERVED_LABELS
            if custom_subdomain in RESERVED_LABELS:
                await ws.send_json({'error': f"Subdomain '{custom_subdomain}' is reserved and cannot be used"})
                await ws.close()
                return ws

            if not await self._check_reserved_subdomain_owner(custom_subdomain, user_id):
                await ws.send_json({'error': 'Subdomain is reserved by another user'})
                await ws.close()
                return ws

            if custom_subdomain in self.tunnels:
                await ws.send_json({'error': 'Subdomain already in use'})
                await ws.close()
                return ws
            subdomain = custom_subdomain
        else:
            subdomain = self.generate_subdomain()

        tunnel_id = str(uuid.uuid4())
        tunnel = TunnelConnection(
            tunnel_id=tunnel_id,
            subdomain=subdomain,
            websocket=ws,
            user_id=user_id,
        )

        self.tunnels[subdomain] = tunnel
        self.request_logs[tunnel_id] = []
        self.pending_responses[tunnel_id] = {}

        logger.info("Tunnel connected: %s (id: %s)", subdomain, tunnel_id)

        # Send connection confirmation
        await ws.send_json({
            'type': 'connected',
            'tunnel_id': tunnel_id,
            'subdomain': subdomain,
            'public_url': tunnel.public_url(self.base_domain),
        })

        try:
            # Keep connection alive, handle responses
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get('type') == 'response':
                        req_id = data.get('request_id')
                        if not req_id:
                            continue

                        fut = self.pending_responses.get(tunnel_id, {}).pop(req_id, None)
                        if fut is None:
                            continue
                        if not fut.done():
                            fut.set_result(data)
                elif msg.type == WSMsgType.ERROR:
                    logger.error("Tunnel error: %s", ws.exception())
        finally:
            # Fail any in-flight requests so callers don't hang.
            pending = self.pending_responses.pop(tunnel_id, {})
            for fut in pending.values():
                if not fut.done():
                    fut.set_result({'status': 502, 'body': 'Connection closed'})

            # Cleanup on disconnect
            if subdomain in self.tunnels:
                del self.tunnels[subdomain]
            logger.info("Tunnel disconnected: %s", subdomain)

        return ws

    async def handle_tunneled_request(self, request: web.Request) -> web.Response:
        """
        Handle HTTP request destined for a tunnel.

        Subdomain is extracted from Host header and request is forwarded
        to the connected tunnel client via WebSocket.
        """
        # Extract subdomain from Host header
        host = request.host
        subdomain = host.split('.')[0]

        if subdomain not in self.tunnels:
            return web.Response(
                status=502,
                text=f"Tunnel not found: {subdomain}",
            )

        tunnel = self.tunnels[subdomain]
        request_id = str(uuid.uuid4())

        # Read request body
        body = await request.read()

        # Log request
        log_entry = RequestLog(
            request_id=request_id,
            tunnel_id=tunnel.tunnel_id,
            method=request.method,
            path=request.path_qs,
            headers=dict(request.headers),
            body=body,
            timestamp=datetime.utcnow(),
        )
        # Cap per-tunnel inspection buffer to prevent unbounded memory growth.
        logs_list = self.request_logs.setdefault(tunnel.tunnel_id, [])
        logs_list.append(log_entry)
        if len(logs_list) > 500:
            del logs_list[:-500]

        # Forward to tunnel client
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        pending = self.pending_responses.get(tunnel.tunnel_id)
        if pending is None:
            pending = {}
            self.pending_responses[tunnel.tunnel_id] = pending

        fut = loop.create_future()
        pending[request_id] = fut

        await tunnel.websocket.send_json({
            'type': 'request',
            'request_id': request_id,
            'method': request.method,
            'path': request.path_qs,
            'headers': dict(request.headers),
            'body': body.decode('utf-8', errors='replace') if body else None,
        })

        tunnel.request_count += 1

        # Wait for response from client (with timeout)
        try:
            response_data = await asyncio.wait_for(fut, timeout=30.0)
        except TimeoutError:
            pending.pop(request_id, None)
            log_entry.response_status = 504
            return web.Response(status=504, text="Tunnel timeout")
        finally:
            pending.pop(request_id, None)

        # Calculate response time
        response_time_ms = int(
            (loop.time() - start_time) * 1000)
        log_entry.response_status = response_data.get('status', 502)
        log_entry.response_time_ms = response_time_ms

        response_body = response_data.get('body', b'')
        if response_body is None:
            response_body = b''
        elif isinstance(response_body, str):
            response_body = response_body.encode('utf-8', errors='replace')

        return web.Response(
            status=response_data.get('status', 502),
            headers=response_data.get('headers', {}),
            body=response_body,
        )

    async def list_tunnels(self, request: web.Request) -> web.Response:
        """List tunnels for the authenticated user."""
        token = request.query.get('token', '')
        tunnel_secret = str(getattr(settings, 'TUNNEL_SECRET', '') or
                            getattr(settings, 'GATEWAY_SECRET', ''))
        payload = self._verify_tunnel_token(token, tunnel_secret)
        if not payload:
            return web.json_response({'error': 'Authentication required'}, status=401)

        user_id = str(payload.get('user_id', ''))
        tunnels = [
            {
                'tunnel_id': t.tunnel_id,
                'subdomain': t.subdomain,
                'public_url': t.public_url(self.base_domain),
                'created_at': t.created_at.isoformat(),
                'request_count': t.request_count,
            }
            for t in self.tunnels.values()
            if t.user_id == user_id
        ]
        return web.json_response({'tunnels': tunnels})

    def _authenticate_http_request(self, request: web.Request) -> dict | None:
        auth = (request.headers.get('Authorization') or '').strip()
        if auth.lower().startswith('bearer '):
            token = auth[7:].strip()
        else:
            token = request.query.get('token', '')
        tunnel_secret = str(
            getattr(settings, 'TUNNEL_SECRET', '')
            or getattr(settings, 'GATEWAY_SECRET', '')
        )
        return self._verify_tunnel_token(token, tunnel_secret)

    async def get_request_logs(self, request: web.Request) -> web.Response:
        """Get request logs for a tunnel owned by the authenticated user."""
        payload = self._authenticate_http_request(request)
        if not payload:
            return web.json_response({'error': 'Authentication required'}, status=401)

        tunnel_id = request.match_info['tunnel_id']
        tunnel = next((t for t in self.tunnels.values() if t.tunnel_id == tunnel_id), None)
        if not tunnel or tunnel.user_id != str(payload.get('user_id', '')):
            return web.json_response({'error': 'Tunnel not found'}, status=404)

        logs = self.request_logs.get(tunnel_id, [])

        return web.json_response({
            'requests': [
                {
                    'request_id': log.request_id,
                    'method': log.method,
                    'path': log.path,
                    'status': log.response_status,
                    'response_time_ms': log.response_time_ms,
                    'timestamp': log.timestamp.isoformat(),
                }
                for log in logs[-100:]  # Last 100 requests
            ]
        })

    async def replay_request(self, request: web.Request) -> web.Response:
        """Replay a logged request for a tunnel owned by the authenticated user."""
        payload = self._authenticate_http_request(request)
        if not payload:
            return web.json_response({'error': 'Authentication required'}, status=401)

        tunnel_id = request.match_info['tunnel_id']
        request_id = request.match_info['request_id']

        # Find active tunnel and verify ownership before reading/replaying
        tunnel = next((t for t in self.tunnels.values() if t.tunnel_id == tunnel_id), None)
        if not tunnel:
            return web.Response(status=404, text="Tunnel not connected")
        if tunnel.user_id != str(payload.get('user_id', '')):
            return web.json_response({'error': 'Forbidden'}, status=403)

        logs = self.request_logs.get(tunnel_id, [])
        log_entry = next((entry for entry in logs if entry.request_id == request_id), None)

        if not log_entry:
            return web.Response(status=404, text="Request not found")

        # Replay the request
        new_request_id = str(uuid.uuid4())
        await tunnel.websocket.send_json({
            'type': 'request',
            'request_id': new_request_id,
            'method': log_entry.method,
            'path': log_entry.path,
            'headers': log_entry.headers,
            'body': log_entry.body.decode('utf-8', errors='replace') if log_entry.body else None,
            'is_replay': True,
        })

        return web.json_response({'status': 'replayed', 'request_id': new_request_id})

    def run(self, host: str = '0.0.0.0', port: int = 8080):
        """Start the tunnel server."""
        logger.info("Starting tunnel server on %s:%s", host, port)
        web.run_app(self.app, host=host, port=port)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    server = TunnelServer()
    server.run()
