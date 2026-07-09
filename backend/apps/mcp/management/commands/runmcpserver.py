import logging

from django.core.management.base import BaseCommand

from apps.mcp.server import mcp_server

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the Model Context Protocol (MCP) server for SMSLY Hosting ecosystem."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sse",
            action="store_true",
            help="Run the server using SSE (Server-Sent Events) over HTTP instead of standard input/output (stdio).",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8001,
            help="Port to listen on when running in SSE mode (default: 8001).",
        )
        parser.add_argument(
            "--host",
            type=str,
            default="127.0.0.1",
            help="Host to bind to when running in SSE mode (default: 127.0.0.1).",
        )

    def handle(self, *args, **options):
        use_sse = options["sse"]
        if use_sse:
            host = options["host"]
            port = options["port"]
            self.stdout.write(self.style.SUCCESS(f"Starting SMSLY Ecosystem MCP Server via SSE on http://{host}:{port}/sse ..."))
            mcp_server.settings.host = host
            mcp_server.settings.port = port
            mcp_server.run(transport="sse")
        else:
            self.stdout.write(self.style.SUCCESS("Starting SMSLY Ecosystem MCP Server via STDIO ..."))
            mcp_server.run(transport="stdio")
