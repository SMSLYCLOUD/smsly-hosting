# pylint: disable=logging-fstring-interpolation
"""Tcp Server module."""
# pylint: disable=broad-exception-caught
"""
SMSLY TCP Tunnel Server

Expose local TCP services (databases, Redis, etc.) to the internet.
Team tier feature.
"""

import asyncio
import logging
import uuid
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger('smsly.tunnels.tcp')


@dataclass
class TCPTunnel:  # pylint: disable=too-many-instance-attributes
    """Represents an active TCP tunnel."""
    tunnel_id: str
    local_port: int
    remote_port: int
    user_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    bytes_in: int = 0
    bytes_out: int = 0
    connections: int = 0
    is_active: bool = True


class TCPTunnelServer:
    """
    TCP tunnel server that forwards connections.

    External client connects to remote_port, traffic is forwarded
    to the tunnel client, which forwards to local_port.
    """

    def __init__(self, host: str = '0.0.0.0',
                 port_range: tuple = (10000, 10999)):
        self.host = host
        self.port_range = port_range
        self.tunnels: Dict[int, TCPTunnel] = {}  # remote_port -> tunnel
        self.available_ports: set = set(
            range(port_range[0], port_range[1] + 1))
        # tunnel_id -> writer
        self.tunnel_writers: Dict[str, asyncio.StreamWriter] = {}

    def allocate_port(self) -> Optional[int]:
        """Allocate an available port."""
        if not self.available_ports:
            return None
        port = self.available_ports.pop()
        return port

    def release_port(self, port: int):
        """Release a port back to the pool."""
        if self.port_range[0] <= port <= self.port_range[1]:
            self.available_ports.add(port)

    async def create_tunnel(self, user_id: str,
                            local_port: int) -> Optional[TCPTunnel]:
        """Create a new TCP tunnel."""
        remote_port = self.allocate_port()
        if not remote_port:
            logger.error("No available ports for TCP tunnel")
            return None

        tunnel = TCPTunnel(
            tunnel_id=str(uuid.uuid4()),
            local_port=local_port,
            remote_port=remote_port,
            user_id=user_id,
        )

        self.tunnels[remote_port] = tunnel

        # Start listening on the remote port
        asyncio.create_task(self._start_listener(tunnel))

        logger.info(
            f"TCP tunnel created: port {remote_port} -> client -> localhost:{local_port}")
        return tunnel

    async def _start_listener(self, tunnel: TCPTunnel,
                              ):  # pylint: disable=unused-argument  # pylint: disable=unused-argument):
        """Start listening for connections on the remote port."""
        try:
            server = await asyncio.start_server(
                lambda r, w: self._handle_connection(tunnel, r, w),
                self.host,
                tunnel.remote_port
            )

            async with server:
                while tunnel.is_active:
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"TCP listener error: {e}")
        finally:
            self.release_port(tunnel.remote_port)

    async def _handle_connection(
        self,
        tunnel: TCPTunnel,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter
    ):  # pylint: disable=unused-argument
        """Handle incoming connection to the TCP tunnel."""
        tunnel.connections += 1
        connection_id = str(uuid.uuid4())[:8]

        logger.info(
            f"TCP connection {connection_id} on port {
                tunnel.remote_port}")

        try:
            # Get the tunnel client writer
            tunnel_writer = self.tunnel_writers.get(tunnel.tunnel_id)
            if not tunnel_writer:
                logger.warning(
                    f"No tunnel client connected for {
                        tunnel.tunnel_id}")
                client_writer.close()
                return

            # Bidirectional forwarding
            await asyncio.gather(
                self._forward(client_reader, tunnel_writer, tunnel, 'in'),
                self._forward_from_tunnel(
                    tunnel.tunnel_id, client_writer, tunnel),
            )

        except Exception as e:
            logger.error(f"TCP connection error: {e}")
        finally:
            client_writer.close()
            tunnel.connections -= 1

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        tunnel: TCPTunnel,
        direction: str
    ):  # pylint: disable=unused-argument
        """Forward data from reader to writer."""
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break

                writer.write(data)
                await writer.drain()

                if direction == 'in':
                    tunnel.bytes_in += len(data)
                else:
                    tunnel.bytes_out += len(data)

        except Exception as e:
            logger.debug(f"Forward ended: {e}")

    async def _forward_from_tunnel(
        self,
        tunnel_id: str,
        client_writer: asyncio.StreamWriter,
        tunnel: TCPTunnel,
    ):  # pylint: disable=unused-argument
        """Forward data from tunnel client to external client."""
        # This would be implemented with actual tunnel client communication
        # For now, placeholder
        await asyncio.sleep(3600)  # Keep connection alive

    async def register_tunnel_client(
        self,
        tunnel_id: str,
        writer: asyncio.StreamWriter
    ):  # pylint: disable=unused-argument
        """Register a tunnel client connection."""
        self.tunnel_writers[tunnel_id] = writer
        logger.info(f"Tunnel client registered: {tunnel_id}")

    async def close_tunnel(self, tunnel_id: str):
        """Close a TCP tunnel."""
        tunnel = next((t for t in self.tunnels.values()
                      if t.tunnel_id == tunnel_id), None)
        if tunnel:
            tunnel.is_active = False
            self.release_port(tunnel.remote_port)
            del self.tunnels[tunnel.remote_port]

            if tunnel_id in self.tunnel_writers:
                self.tunnel_writers[tunnel_id].close()
                del self.tunnel_writers[tunnel_id]

            logger.info(f"TCP tunnel closed: {tunnel_id}")

    def get_tunnel_info(self, tunnel_id: str) -> Optional[dict]:
        """Get tunnel information."""
        tunnel = next((t for t in self.tunnels.values()
                      if t.tunnel_id == tunnel_id), None)
        if not tunnel:
            return None

        return {
            'tunnel_id': tunnel.tunnel_id,
            'type': 'tcp',
            'local_port': tunnel.local_port,
            'remote_port': tunnel.remote_port,
            'public_host': f"tcp.tunnel.smsly.cloud:{tunnel.remote_port}",
            'bytes_in': tunnel.bytes_in,
            'bytes_out': tunnel.bytes_out,
            'connections': tunnel.connections,
            'is_active': tunnel.is_active,
            'created_at': tunnel.created_at.isoformat(),
        }


# CLI support for TCP tunnels
TCP_HELP = """
TCP Tunnel Usage:

  smsly-tunnel tcp 5432
  → Creates: tcp.tunnel.smsly.cloud:10XXX

  # Connect your database client to:
  # tcp.tunnel.smsly.cloud:10XXX

Examples:
  # PostgreSQL
  smsly-tunnel tcp 5432
  psql -h tcp.tunnel.smsly.cloud -p 10001 -U user dbname

  # Redis
  smsly-tunnel tcp 6379
  redis-cli -h tcp.tunnel.smsly.cloud -p 10002

  # MySQL
  smsly-tunnel tcp 3306
  mysql -h tcp.tunnel.smsly.cloud -P 10003 -u user -p
"""
