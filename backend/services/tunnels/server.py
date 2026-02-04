# pylint: disable=logging-fstring-interpolation
"""Server module."""
"""
SMSLY Tunnels - WebSocket Tunnel Server

Expose local development servers to public URLs via WebSocket tunnels.
Similar to ngrok, but integrated with SMSLY Hosting.
"""

import asyncio
import json
import uuid
import logging
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
from aiohttp import web, WSMsgType

logger = logging.getLogger('smsly.tunnels')


@dataclass
class TunnelConnection:  # pylint: disable=too-many-instance-attributes
    """Represents an active tunnel connection."""
    tunnel_id: str
    subdomain: str
    websocket: web.WebSocketResponse
    user_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    request_count: int = 0

    def public_url(self, base_domain: str = "tunnel.smsly.cloud") -> str:
        """Get public URL."""
        return f"https://{self.subdomain}.{base_domain}"


@dataclass
class RequestLog:  # pylint: disable=too-many-instance-attributes
    """Logged HTTP request for inspection."""
    request_id: str
    tunnel_id: str
    method: str
    path: str
    headers: Dict[str, str]
    body: bytes
    timestamp: datetime
    response_status: Optional[int] = None
    response_time_ms: Optional[int] = None


class TunnelServer:  # pylint: disable=too-many-instance-attributes
    """
    WebSocket-based tunnel server.

    Routes incoming HTTP requests to connected tunnel clients.
    """

    def __init__(self, base_domain: str = "tunnel.smsly.cloud"):
        self.base_domain = base_domain
        self.tunnels: Dict[str, TunnelConnection] = {}  # subdomain -> tunnel
        self.request_logs: Dict[str, list] = {}  # tunnel_id -> [RequestLog]
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

    async def handle_tunnel_connect(self, request: web.Request) -> web.WebSocketResponse:
        """
        Handle new tunnel WebSocket connection from CLI client.

        Client connects and receives assigned subdomain.
        Future HTTP requests to that subdomain are forwarded via WebSocket.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Get optional custom subdomain from query
        custom_subdomain = request.query.get('subdomain')
        user_id = request.query.get('user_id')  # From auth token

        if custom_subdomain:
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
                        # Response from local server, handled separately
                        pass
                elif msg.type == WSMsgType.ERROR:
                    logger.error("Tunnel error: %s", ws.exception())
        finally:
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
        self.request_logs[tunnel.tunnel_id].append(log_entry)

        # Forward to tunnel client
        start_time = asyncio.get_event_loop().time()

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
            response_data = await asyncio.wait_for(
                self._wait_for_response(tunnel.websocket, request_id),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            log_entry.response_status = 504
            return web.Response(status=504, text="Tunnel timeout")

        # Calculate response time
        response_time_ms = int(
            (asyncio.get_event_loop().time() - start_time) * 1000)
        log_entry.response_status = response_data.get('status', 502)
        log_entry.response_time_ms = response_time_ms

        return web.Response(
            status=response_data.get('status', 502),
            headers=response_data.get('headers', {}),
            body=response_data.get('body', b''),
        )

    async def _wait_for_response(self, ws: web.WebSocketResponse, request_id: str) -> Dict:
        """Wait for response message matching request_id."""
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get('type') == 'response' and data.get(
                        'request_id') == request_id:
                    return data
        return {'status': 502, 'body': b'Connection closed'}

    async def list_tunnels(self, request: web.Request) -> web.Response: # pylint: disable=unused-argument
        """List all active tunnels (for dashboard)."""
        tunnels = [
            {
                'tunnel_id': t.tunnel_id,
                'subdomain': t.subdomain,
                'public_url': t.public_url(self.base_domain),
                'created_at': t.created_at.isoformat(),
                'request_count': t.request_count,
            }
            for t in self.tunnels.values()
        ]
        return web.json_response({'tunnels': tunnels})

    async def get_request_logs(self, request: web.Request) -> web.Response:
        """Get request logs for a tunnel (for inspector)."""
        tunnel_id = request.match_info['tunnel_id']
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
        """Replay a logged request."""
        tunnel_id = request.match_info['tunnel_id']
        request_id = request.match_info['request_id']

        logs = self.request_logs.get(tunnel_id, [])
        log_entry = next((l for l in logs if l.request_id == request_id), None)

        if not log_entry:
            return web.Response(status=404, text="Request not found")

        # Find active tunnel
        tunnel = next((t for t in self.tunnels.values() if t.tunnel_id == tunnel_id), None)

        if not tunnel:
            return web.Response(status=404, text="Tunnel not connected")

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
