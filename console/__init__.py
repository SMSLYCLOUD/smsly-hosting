"""
Service Console Monitoring System

This package provides monitoring, logging, and diagnostic tools for the
WebSocket-based service console to detect and troubleshoot connection issues.
"""

__version__ = "1.0.0"

# Import key components for easy access
from . import config
from .monitor import get_monitor
from .diagnostics import run_diagnostics, analyze_disconnect
from .integration import apply_monitoring

# Apply monitoring when imported (if auto-apply is enabled)
if getattr(config, 'AUTO_APPLY_MONITORING', False):
    apply_monitoring()