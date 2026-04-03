"""
API endpoints for accessing monitoring data.

This module provides Django REST Framework views for accessing
the monitoring data collected by the monitoring system.
"""

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.request import Request

from . import monitor, diagnostics, config


class MonitoringViewSet(viewsets.ViewSet):
    """
    ViewSet for accessing monitoring data.
    
    This ViewSet provides endpoints for accessing the monitoring data
    collected by the monitoring system.
    """
    
    permission_classes = [permissions.IsAdminUser]
    
    def list(self, request: Request) -> Response:
        """List basic monitoring information."""
        mon = monitor.get_monitor()
        
        # Get session stats
        session_stats = mon.get_all_session_stats()
        
        # Get disconnect patterns
        patterns = mon.get_disconnect_patterns()
        
        return Response({
            "session_count": len(session_stats),
            "active_sessions": sum(1 for s in session_stats if s["is_active"]),
            "disconnect_patterns": patterns,
            "config": {
                "idle_timeout": config.WEBSOCKET_IDLE_TIMEOUT,
                "max_reconnect_attempts": config.MAX_RECONNECT_ATTEMPTS,
                "detailed_logging": config.ENABLE_DETAILED_LOGGING,
                "diagnostic_mode": config.DIAGNOSTIC_MODE
            }
        })
    
    @action(detail=False, methods=["get"])
    def sessions(self, request: Request) -> Response:
        """Get all session statistics."""
        mon = monitor.get_monitor()
        session_stats = mon.get_all_session_stats()
        return Response(session_stats)
    
    @action(detail=True, methods=["get"])
    def session(self, request: Request, pk: str = None) -> Response:
        """Get statistics for a specific session."""
        if ":" not in pk:
            return Response({"error": "Invalid session ID format. Use deployment_id:user_id"}, status=400)
        
        deployment_id, user_id = pk.split(":", 1)
        
        mon = monitor.get_monitor()
        stats = mon.get_session_stats(deployment_id, user_id)
        
        if not stats:
            return Response({"error": "Session not found"}, status=404)
        
        return Response(stats)
    
    @action(detail=False, methods=["get"])
    def events(self, request: Request) -> Response:
        """Get recent connection events."""
        mon = monitor.get_monitor()
        
        # Get count parameter, default to 100
        count = request.query_params.get("count", 100)
        try:
            count = int(count)
        except ValueError:
            count = 100
        
        events = mon.get_recent_events(count)
        return Response(events)
    
    @action(detail=False, methods=["get"])
    def patterns(self, request: Request) -> Response:
        """Get disconnect patterns."""
        mon = monitor.get_monitor()
        patterns = mon.get_disconnect_patterns()
        return Response(patterns)
    
    @action(detail=False, methods=["post"])
    def run_diagnostics(self, request: Request) -> Response:
        """Run diagnostics for a WebSocket URL."""
        ws_url = request.data.get("ws_url")
        user_agent = request.data.get("user_agent")
        
        if not ws_url:
            return Response({"error": "ws_url is required"}, status=400)
        
        results = diagnostics.run_diagnostics(ws_url, user_agent)
        return Response(results)
    
    @action(detail=False, methods=["post"])
    def analyze_disconnect(self, request: Request) -> Response:
        """Analyze a WebSocket disconnect event."""
        close_code = request.data.get("close_code")
        close_reason = request.data.get("close_reason", "Unknown reason")
        
        if close_code is None:
            return Response({"error": "close_code is required"}, status=400)
        
        try:
            close_code = int(close_code)
        except ValueError:
            return Response({"error": "close_code must be an integer"}, status=400)
        
        analysis = diagnostics.analyze_disconnect(close_code, close_reason)
        return Response(analysis)
    
    @action(detail=False, methods=["post"])
    def update_config(self, request: Request) -> Response:
        """Update monitoring configuration."""
        # Only allow updating certain config values
        allowed_keys = [
            "WEBSOCKET_IDLE_TIMEOUT",
            "MAX_RECONNECT_ATTEMPTS",
            "ENABLE_DETAILED_LOGGING",
            "LOG_CONNECTION_EVENTS",
            "MONITOR_LATENCY",
            "CONNECTION_ALERT_THRESHOLD",
            "DIAGNOSTIC_MODE"
        ]
        
        updated = {}
        for key in allowed_keys:
            if key in request.data:
                value = request.data[key]
                
                # Type conversion based on current type
                current_value = getattr(config, key, None)
                if current_value is not None:
                    if isinstance(current_value, bool):
                        value = bool(value)
                    elif isinstance(current_value, int):
                        value = int(value)
                    elif isinstance(current_value, float):
                        value = float(value)
                
                # Update config
                setattr(config, key, value)
                updated[key] = value
        
        return Response({
            "updated": updated,
            "config": {
                key: getattr(config, key) for key in allowed_keys
            }
        })