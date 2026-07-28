"""
Proxy mixin for ManagedServerViewSet.
"""

import json as json_mod
import posixpath
from urllib.parse import urlparse

import requests
from rest_framework import status
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response

from .helpers import (
    _build_remote_headers,
    _lite_agent_proxy_response,
    _proxy_error_response,
)
from .serializers import (
    ALLOWED_PROXY_METHODS,
    ALLOWED_PROXY_PATHS,
    ServerProxyThrottle,
)

MAX_PROXY_BODY_SIZE = 1_048_576


class ProxyMixin:

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerProxyThrottle])
    def proxy(self, request, pk=None):
        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        if method not in ALLOWED_PROXY_METHODS:
            return Response(
                {"error": f"Method {method} is not allowed."},
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        raw_path = str(request.data.get("path", "") or "")
        body = request.data.get("body")

        if body is not None:
            serialized = json_mod.dumps(body, sort_keys=True)
            if len(serialized.encode('utf-8')) > MAX_PROXY_BODY_SIZE:
                return Response(
                    {"error": f"Proxy body too large; max {MAX_PROXY_BODY_SIZE} bytes."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

        path_part, _, query_part = raw_path.partition("?")
        normalized_path = posixpath.normpath(path_part or "/")
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        path = f"{normalized_path}?{query_part}" if query_part else normalized_path

        if ".." in path:
            return Response(
                {"error": "Directory traversal is not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        path_only_for_match = normalized_path.rstrip("/")
        if not any(
            path_only_for_match == allowed.rstrip("/")
            or path_only_for_match.startswith(allowed.rstrip("/") + "/")
            for allowed in ALLOWED_PROXY_PATHS
        ):
            return Response(
                {"error": "Path not in proxy allowlist."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not path.startswith("/api/"):
            return Response(
                {"error": "Only /api/ paths can be proxied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lite_agent_response = _lite_agent_proxy_response(server, request, method, path)
        if lite_agent_response is not None:
            return lite_agent_response

        parsed = urlparse(server.api_url)
        if parsed.scheme not in ("http", "https"):
            return Response(
                {"error": "Server API URL must use HTTP or HTTPS."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_host = (parsed.hostname or "").strip().lower()
        server_host = (server.host or "").strip().lower()
        if not server_host:
            return Response(
                {"error": "Server host is not configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if api_host != server_host:
            return Response(
                {
                    "error": (
                        "api_url hostname does not match server.host; "
                        "refusing to forward authenticated proxy request."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = f"{server.api_url.rstrip('/')}{path}"
        body_bytes = json_mod.dumps(body, sort_keys=True).encode() if body is not None else b""
        headers = _build_remote_headers(server, method=method, path=path, body=body_bytes)

        try:
            resp = requests.request(
                method, url,
                headers=headers,
                data=body_bytes if body is not None else None,
                timeout=30,
            )
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:2000]}

            return Response({
                "status_code": resp.status_code,
                "data": data,
            })
        except requests.RequestException as e:
            return _proxy_error_response(f"Proxy request failed: {e!s}")
