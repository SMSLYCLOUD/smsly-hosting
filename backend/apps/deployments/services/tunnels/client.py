#!/usr/bin/env python3
# pylint: disable=broad-exception-caught, too-many-branches
"""
SMSLY Tunnel CLI Client

Connect your local development server to a public URL.

Usage:
    smsly-tunnel <port> [--subdomain <name>] [--inspect]

Examples:
    smsly-tunnel 3000
    smsly-tunnel 3000 --subdomain myapp
    smsly-tunnel 3000 --inspect
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal

import aiohttp

# Rich console for pretty output (fallback to basic if not installed)
try:
    from rich.console import Console
    # pylint: disable=unused-import
    CONSOLE = Console()
    RICH_AVAILABLE = True
except ImportError:
    CONSOLE = None
    RICH_AVAILABLE = False

logger = logging.getLogger('smsly.tunnel.client')


def default_tunnel_server_url() -> str:
    """Resolve the tunnel server URL without a vendor-specific hardcoded host."""
    explicit_url = (
        os.environ.get('SMSLY_TUNNEL_SERVER_URL')
        or os.environ.get('TUNNEL_SERVER_URL')
    )
    if explicit_url:
        return explicit_url

    tunnel_domain = os.environ.get('TUNNEL_DOMAIN') or os.environ.get('DOMAIN')
    if tunnel_domain:
        return f"wss://{tunnel_domain}/ws/tunnel"

    return "ws://localhost:8080/ws/tunnel"


class TunnelClient:
    """
    CLI tunnel client that connects to SMSLY tunnel server
    and forwards requests to local development server.
    """

    def __init__(
        self,
        local_port: int,
        server_url: str | None = None,
        subdomain: str | None = None,
        inspect: bool = False,
    ):
        self.local_port = local_port
        self.server_url = server_url or default_tunnel_server_url()
        self.subdomain = subdomain
        self.inspect = inspect
        self.public_url: str | None = None
        self.request_count = 0
        self._running = False

    def print_banner(self):
        """Print startup banner."""
        if RICH_AVAILABLE:
            CONSOLE.print("\n[bold blue]SMSLY Tunnel[/bold blue]")
            CONSOLE.print("─" * 40)
        else:
            print("\nSMSLY Tunnel")
            print("-" * 40)

    def print_connected(self, data: dict):
        """Print connection success message."""
        self.public_url = data.get('public_url')

        if RICH_AVAILABLE and CONSOLE is not None:
            CONSOLE.print("[green]✓[/green] Tunnel established")
            CONSOLE.print(f"[bold]→[/bold] {self.public_url}")
            CONSOLE.print(f"[dim]  Forwarding to localhost:{self.local_port}[/dim]")
            CONSOLE.print()
            CONSOLE.print("[dim]Press Ctrl+C to stop[/dim]")
        else:
            print("✓ Tunnel established")
            print(f"→ {self.public_url}")
            print(f"  Forwarding to localhost:{self.local_port}")
            print()
            print("Press Ctrl+C to stop")

    def print_request(self, data: dict):
        """Print incoming request (if inspect mode)."""
        if not self.inspect:
            return

        self.request_count += 1
        method = data.get('method', 'GET')
        path = data.get('path', '/')

        if RICH_AVAILABLE and CONSOLE is not None:
            color = "green" if method == "GET" else "yellow" if method == "POST" else "blue"
            CONSOLE.print(f"[{color}]{method}[/{color}] {path}")
        else:
            print(f"{method} {path}")

    async def forward_request(self, session: aiohttp.ClientSession, data: dict) -> dict:
        """Forward request to local server and return response."""
        method = data.get('method', 'GET')
        path = data.get('path', '/')
        headers = data.get('headers', {})
        body = data.get('body')

        # Remove host header to avoid conflicts
        headers.pop('Host', None)
        headers.pop('host', None)

        local_url = f"http://localhost:{self.local_port}{path}"

        try:
            async with session.request(
                method=method,
                url=local_url,
                headers=headers,
                data=body.encode() if body else None,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                response_body = await resp.read()
                return {
                    'type': 'response',
                    'request_id': data.get('request_id'),
                    'status': resp.status,
                    'headers': dict(resp.headers),
                    'body': response_body.decode('utf-8', errors='replace'),
                }
        except aiohttp.ClientConnectorError:
            return {
                'type': 'response',
                'request_id': data.get('request_id'),
                'status': 502,
                'body': f'Cannot connect to localhost:{self.local_port}',
            }
        except Exception as e: # pylint: disable=broad-exception-caught
            return {
                'type': 'response',
                'request_id': data.get('request_id'),
                'status': 500,
                'body': str(e),
            }

    async def run(self):
        """Main client loop."""
        self._running = True
        self.print_banner()

        # Build WebSocket URL with query params
        ws_url = self.server_url
        if self.subdomain:
            ws_url += f"?subdomain={self.subdomain}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(ws_url) as ws:
                    async for msg in ws:
                        if not self._running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)

                            if data.get('type') == 'connected':
                                self.print_connected(data)

                            elif data.get('type') == 'request':
                                self.print_request(data)
                                response = await self.forward_request(session, data)
                                await ws.send_json(response)

                            elif data.get('error'):
                                if RICH_AVAILABLE:
                                    CONSOLE.print(f"[red]Error:[/red] {data.get('error')}")
                                else:
                                    print(f"Error: {data.get('error')}")
                                break

                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            if RICH_AVAILABLE:
                                CONSOLE.print("[red]Connection error[/red]")
                            else:
                                print("Connection error")
                            break

            except aiohttp.ClientConnectorError:
                if RICH_AVAILABLE:
                    CONSOLE.print("[red]Cannot connect to tunnel server[/red]")
                else:
                    print("Cannot connect to tunnel server")
            except KeyboardInterrupt:
                pass

        self._running = False
        if RICH_AVAILABLE:
            CONSOLE.print("\n[dim]Tunnel closed[/dim]")
        else:
            print("\nTunnel closed")

    def stop(self):
        """Stop the tunnel client."""
        self._running = False


def main():
    """Entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Expose local server to public URL via SMSLY Tunnel"
    )
    parser.add_argument(
        'port',
        type=int,
        help="Local port to tunnel"
    )
    parser.add_argument(
        '--subdomain',
        type=str,
        help="Custom subdomain (default: auto-generated)"
    )
    parser.add_argument(
        '--server',
        type=str,
        default=default_tunnel_server_url(),
        help="Tunnel server URL"
    )
    parser.add_argument(
        '--inspect',
        action='store_true',
        help="Show incoming requests"
    )
    parser.add_argument(
        '--local',
        action='store_true',
        help="Use local dev server (ws://localhost:8080)"
    )

    args = parser.parse_args()

    server_url = args.server
    if args.local:
        server_url = "ws://localhost:8080/ws/tunnel"

    client = TunnelClient(
        local_port=args.port,
        server_url=server_url,
        subdomain=args.subdomain,
        inspect=args.inspect,
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame): # pylint: disable=unused-argument
        client.stop()

    signal.signal(signal.SIGINT, signal_handler)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(client.run())


if __name__ == '__main__':
    main()
