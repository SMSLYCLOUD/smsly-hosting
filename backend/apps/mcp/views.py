"""HTTP API for MCP server control, tool discovery, and tool execution."""

import inspect
import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}

# Internal plumbing params every tool accepts — never exposed to callers.
_HIDDEN_PARAMS = {"user_id", "user_email"}


def _discover_tools() -> dict:
    """Public tool functions in apps.mcp.tools, keyed by name.

    Only functions actually defined in that module (no re-exported
    helpers, no private names) are callable through the API.
    """
    from apps.mcp import tools as tools_module
    found = {}
    for name, func in inspect.getmembers(tools_module, inspect.isfunction):
        if name.startswith("_"):
            continue
        if getattr(func, "__module__", "") != tools_module.__name__:
            continue
        found[name] = func
    return found


def _describe_tool(name: str, func) -> dict:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {"name": name, "description": inspect.getdoc(func) or "", "params": []}
    params = []
    for pname, param in sig.parameters.items():
        if pname in _HIDDEN_PARAMS:
            continue
        ann = param.annotation
        ann_name = getattr(ann, "__name__", "") if ann is not inspect.Parameter.empty else ""
        params.append({
            "name": pname,
            "type": _TYPE_MAP.get(ann_name, "string"),
            "required": param.default is inspect.Parameter.empty,
            "default": None if param.default is inspect.Parameter.empty else param.default,
        })
    return {
        "name": name,
        "description": (inspect.getdoc(func) or "").strip(),
        "params": params,
    }


def _coerce(value, type_name: str):
    """Best-effort coercion of JSON-decoded values to the declared type."""
    if value is None or type_name in ("string", "object", "array"):
        return value
    try:
        if type_name == "integer":
            return int(value)
        if type_name == "number":
            return float(value)
        if type_name == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
    except (TypeError, ValueError):
        return value
    return value


class McpStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.mcp import services as mcp_services
        from apps.mcp import server as server_module
        payload = mcp_services.get_status()
        payload["tools_count"] = len(_discover_tools())
        payload["fastmcp_available"] = bool(getattr(server_module, "_MCP_AVAILABLE", False))
        return Response(payload)


class McpControlView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.mcp import services as mcp_services
        action = str(request.data.get("action") or "").strip().lower()
        try:
            if action == "start":
                payload = mcp_services.start()
            elif action == "stop":
                payload = mcp_services.stop()
            elif action == "restart":
                payload = mcp_services.restart()
            else:
                return Response(
                    {"error": "action must be one of: start, stop, restart."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except RuntimeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        except Exception as exc:
            logger.exception("MCP control action %s failed", action)
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        payload["tools_count"] = len(_discover_tools())
        return Response(payload)


class McpToolListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tools = [_describe_tool(name, func) for name, func in sorted(_discover_tools().items())]
        return Response({"tools": tools, "count": len(tools)})


class McpToolCallView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, name: str):
        tools = _discover_tools()
        func = tools.get(name)
        if func is None:
            return Response(
                {"ok": False, "error": f"Unknown tool: {name}."},
                status=status.HTTP_404_NOT_FOUND,
            )
        args = request.data.get("args") or {}
        if not isinstance(args, dict):
            return Response(
                {"ok": False, "error": "args must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Coerce declared scalar params; inject the caller's identity for
        # the tools' built-in per-object permission checks.
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            sig = None
        call_args: dict = {}
        if sig is not None:
            for pname, param in sig.parameters.items():
                if pname in _HIDDEN_PARAMS:
                    continue
                if pname in args:
                    ann = param.annotation
                    ann_name = getattr(ann, "__name__", "") if ann is not inspect.Parameter.empty else ""
                    call_args[pname] = _coerce(args[pname], _TYPE_MAP.get(ann_name, "string"))
                elif param.default is not inspect.Parameter.empty:
                    call_args[pname] = param.default
        else:
            call_args = dict(args)
        call_args["user_id"] = str(request.user.id)
        try:
            result = func(**call_args)
        except TypeError as exc:
            return Response(
                {"ok": False, "error": f"Bad arguments: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.exception("MCP tool %s failed", name)
            return Response({"ok": False, "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"ok": True, "result": result})
