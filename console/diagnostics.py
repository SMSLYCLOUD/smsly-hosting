"""
Diagnostic tools for troubleshooting WebSocket connection issues.

This module provides utilities to capture diagnostic information about
WebSocket connections, network conditions, and client environments to
help diagnose service console disconnect issues.
"""

import json
import logging
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from . import config

logger = logging.getLogger("console.diagnostics")

class NetworkDiagnostics:
    """Tools for diagnosing network-related issues."""
    
    @staticmethod
    def check_connectivity(host: str, port: int) -> Dict[str, Any]:
        """Check basic connectivity to a host and port."""
        result = {
            "host": host,
            "port": port,
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "latency_ms": None,
            "error": None
        }
        
        try:
            start_time = time.time()
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((host, port))
            latency = (time.time() - start_time) * 1000
            s.close()
            
            result["success"] = True
            result["latency_ms"] = latency
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    @staticmethod
    def run_traceroute(host: str) -> Dict[str, Any]:
        """Run a traceroute to the host and capture results."""
        result = {
            "host": host,
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "hops": [],
            "error": None
        }
        
        try:
            # Use tracert on Windows, traceroute on other platforms
            cmd = ["tracert", "-d", "-w", "500", host] if platform.system() == "Windows" else ["traceroute", "-n", host]
            
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate(timeout=30)
            
            if process.returncode == 0:
                result["success"] = True
                result["output"] = stdout
                
                # Parse the output to extract hops (simplified)
                lines = stdout.splitlines()
                for line in lines:
                    if line.strip() and any(c.isdigit() for c in line):
                        result["hops"].append(line.strip())
            else:
                result["error"] = stderr or f"Command failed with return code {process.returncode}"
        
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    @staticmethod
    def check_dns(hostname: str) -> Dict[str, Any]:
        """Check DNS resolution for a hostname."""
        result = {
            "hostname": hostname,
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "addresses": [],
            "error": None
        }
        
        try:
            start_time = time.time()
            addresses = socket.getaddrinfo(hostname, None)
            latency = (time.time() - start_time) * 1000
            
            # Extract unique IP addresses
            ips = set()
            for addr in addresses:
                ip = addr[4][0]
                if ip:
                    ips.add(ip)
            
            result["success"] = True
            result["addresses"] = list(ips)
            result["latency_ms"] = latency
        
        except Exception as e:
            result["error"] = str(e)
        
        return result


class WebSocketDiagnostics:
    """Tools for diagnosing WebSocket-specific issues."""
    
    @staticmethod
    def capture_connection_info(ws_url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Capture information about a WebSocket connection attempt.
        
        This is a placeholder - in a real implementation, you would use a WebSocket
        client library to attempt a connection and capture detailed information.
        """
        return {
            "url": ws_url,
            "timestamp": datetime.now().isoformat(),
            "headers": headers or {},
            "note": "This is a placeholder. Implement with actual WebSocket client library."
        }
    
    @staticmethod
    def analyze_disconnect_reason(close_code: int, close_reason: str) -> Dict[str, Any]:
        """Analyze a WebSocket close code and reason to provide diagnostic information."""
        result = {
            "close_code": close_code,
            "close_reason": close_reason,
            "timestamp": datetime.now().isoformat(),
            "standard_meaning": "Unknown",
            "likely_causes": [],
            "suggested_actions": []
        }
        
        # Standard WebSocket close codes
        if close_code == 1000:
            result["standard_meaning"] = "Normal Closure"
            result["likely_causes"] = ["User closed the connection", "Application completed normally"]
            result["suggested_actions"] = ["No action needed"]
        
        elif close_code == 1001:
            result["standard_meaning"] = "Going Away"
            result["likely_causes"] = ["Server is shutting down", "Browser navigating away"]
            result["suggested_actions"] = ["Reconnect if needed", "Check server status"]
        
        elif close_code == 1002:
            result["standard_meaning"] = "Protocol Error"
            result["likely_causes"] = ["WebSocket protocol violation", "Malformed frame"]
            result["suggested_actions"] = ["Check client WebSocket implementation", "Update client libraries"]
        
        elif close_code == 1003:
            result["standard_meaning"] = "Unsupported Data"
            result["likely_causes"] = ["Received data in an unsupported format"]
            result["suggested_actions"] = ["Check message format being sent"]
        
        elif close_code == 1005:
            result["standard_meaning"] = "No Status Received"
            result["likely_causes"] = ["Connection closed without a status code"]
            result["suggested_actions"] = ["Check for network interruptions", "Check proxy configurations"]
        
        elif close_code == 1006:
            result["standard_meaning"] = "Abnormal Closure"
            result["likely_causes"] = [
                "Connection dropped without close frame", 
                "Network interruption",
                "Proxy timeout",
                "Cloudflare WebSocket timeout (if using Cloudflare)"
            ]
            result["suggested_actions"] = [
                "Check network stability",
                "Check for proxy timeouts",
                "If using Cloudflare, check WebSocket timeout settings",
                "Implement more aggressive keepalive mechanism"
            ]
        
        elif close_code == 1007:
            result["standard_meaning"] = "Invalid frame payload data"
            result["likely_causes"] = ["Message contained invalid data"]
            result["suggested_actions"] = ["Check message encoding", "Validate message content"]
        
        elif close_code == 1008:
            result["standard_meaning"] = "Policy Violation"
            result["likely_causes"] = ["Message violated policy", "Server rejected message"]
            result["suggested_actions"] = ["Check message content against server policies"]
        
        elif close_code == 1009:
            result["standard_meaning"] = "Message Too Big"
            result["likely_causes"] = ["Message exceeded size limits"]
            result["suggested_actions"] = ["Reduce message size", "Split large messages"]
        
        elif close_code == 1010:
            result["standard_meaning"] = "Missing Extension"
            result["likely_causes"] = ["Client requested extension server doesn't support"]
            result["suggested_actions"] = ["Check WebSocket extension requirements"]
        
        elif close_code == 1011:
            result["standard_meaning"] = "Internal Error"
            result["likely_causes"] = ["Server encountered an error", "Unexpected condition"]
            result["suggested_actions"] = ["Check server logs", "Report issue to server administrators"]
        
        elif close_code == 1012:
            result["standard_meaning"] = "Service Restart"
            result["likely_causes"] = ["Server is restarting"]
            result["suggested_actions"] = ["Reconnect after a delay"]
        
        elif close_code == 1013:
            result["standard_meaning"] = "Try Again Later"
            result["likely_causes"] = ["Server is temporarily unavailable"]
            result["suggested_actions"] = ["Reconnect with exponential backoff"]
        
        elif close_code == 1014:
            result["standard_meaning"] = "Bad Gateway"
            result["likely_causes"] = ["Proxy or gateway error"]
            result["suggested_actions"] = ["Check proxy configuration", "Verify gateway status"]
        
        elif close_code == 1015:
            result["standard_meaning"] = "TLS Handshake Failure"
            result["likely_causes"] = ["TLS/SSL handshake failed"]
            result["suggested_actions"] = ["Check SSL/TLS configuration", "Verify certificates"]
        
        # Cloudflare-specific codes
        elif close_code == 1001 and "cloudflare" in close_reason.lower():
            result["standard_meaning"] = "Cloudflare Timeout"
            result["likely_causes"] = ["Cloudflare WebSocket timeout (default 5 minutes)"]
            result["suggested_actions"] = [
                "Implement more frequent heartbeats (every 30 seconds)",
                "Configure Cloudflare for longer WebSocket timeouts if possible"
            ]
        
        # Custom application codes (4000-4999)
        elif 4000 <= close_code < 5000:
            result["standard_meaning"] = "Application-Defined Error"
            
            # Add specific meanings for our application codes
            if close_code == 4000:
                result["standard_meaning"] = "Session Timeout"
                result["likely_causes"] = ["User session timed out due to inactivity"]
                result["suggested_actions"] = ["Reconnect and reauthenticate"]
            
            elif close_code == 4001:
                result["standard_meaning"] = "Authentication Required"
                result["likely_causes"] = ["Missing authentication token"]
                result["suggested_actions"] = ["Provide valid authentication token"]
            
            elif close_code == 4002:
                result["standard_meaning"] = "Invalid Authentication"
                result["likely_causes"] = ["Invalid or expired authentication token"]
                result["suggested_actions"] = ["Refresh authentication token and reconnect"]
            
            elif close_code == 4003:
                result["standard_meaning"] = "Authorization Failed"
                result["likely_causes"] = ["User not authorized for this resource"]
                result["suggested_actions"] = ["Check user permissions"]
        
        return result


class SystemDiagnostics:
    """Tools for diagnosing system-related issues."""
    
    @staticmethod
    def capture_system_info() -> Dict[str, Any]:
        """Capture information about the system environment."""
        return {
            "timestamp": datetime.now().isoformat(),
            "platform": platform.platform(),
            "python_version": sys.version,
            "hostname": socket.gethostname(),
            "processor": platform.processor(),
            "machine": platform.machine()
        }
    
    @staticmethod
    def capture_client_info(user_agent: str) -> Dict[str, Any]:
        """
        Parse and capture information from a user agent string.
        
        This is a simplified implementation - in a real system, you would
        use a proper user agent parser library.
        """
        result = {
            "user_agent": user_agent,
            "timestamp": datetime.now().isoformat(),
            "browser": "Unknown",
            "browser_version": "Unknown",
            "os": "Unknown",
            "device": "Unknown"
        }
        
        # Very basic parsing - not comprehensive
        user_agent = user_agent.lower()
        
        if "firefox" in user_agent:
            result["browser"] = "Firefox"
        elif "chrome" in user_agent and "edge" not in user_agent:
            result["browser"] = "Chrome"
        elif "safari" in user_agent and "chrome" not in user_agent:
            result["browser"] = "Safari"
        elif "edge" in user_agent:
            result["browser"] = "Edge"
        elif "opera" in user_agent or "opr" in user_agent:
            result["browser"] = "Opera"
        
        if "windows" in user_agent:
            result["os"] = "Windows"
        elif "macintosh" in user_agent or "mac os" in user_agent:
            result["os"] = "macOS"
        elif "linux" in user_agent:
            result["os"] = "Linux"
        elif "android" in user_agent:
            result["os"] = "Android"
            result["device"] = "Mobile"
        elif "iphone" in user_agent or "ipad" in user_agent:
            result["os"] = "iOS"
            result["device"] = "iPhone" if "iphone" in user_agent else "iPad"
        
        if "mobile" in user_agent and result["device"] == "Unknown":
            result["device"] = "Mobile"
        elif "tablet" in user_agent and result["device"] == "Unknown":
            result["device"] = "Tablet"
        elif result["device"] == "Unknown":
            result["device"] = "Desktop"
        
        return result


def run_diagnostics(ws_url: str, user_agent: Optional[str] = None) -> Dict[str, Any]:
    """
    Run a comprehensive set of diagnostics for WebSocket connection issues.
    
    Args:
        ws_url: The WebSocket URL to diagnose
        user_agent: Optional user agent string from the client
    
    Returns:
        A dictionary with diagnostic results
    """
    if not config.CAPTURE_NETWORK_STATS and not config.CAPTURE_CLIENT_INFO:
        return {"error": "Diagnostics disabled in configuration"}
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "websocket_url": ws_url
    }
    
    # Parse the URL to get host and port
    try:
        from urllib.parse import urlparse
        parsed = urlparse(ws_url)
        protocol = parsed.scheme
        hostname = parsed.netloc.split(':')[0]
        port = parsed.port or (443 if protocol in ('wss', 'https') else 80)
        
        if config.CAPTURE_NETWORK_STATS:
            # Network diagnostics
            results["connectivity"] = NetworkDiagnostics.check_connectivity(hostname, port)
            results["dns"] = NetworkDiagnostics.check_dns(hostname)
            
            # Only run traceroute if explicitly enabled (it's slow and noisy)
            if config.DIAGNOSTIC_MODE:
                results["traceroute"] = NetworkDiagnostics.run_traceroute(hostname)
        
        if config.CAPTURE_CLIENT_INFO and user_agent:
            # Client diagnostics
            results["client_info"] = SystemDiagnostics.capture_client_info(user_agent)
        
        # System info
        results["system_info"] = SystemDiagnostics.capture_system_info()
        
    except Exception as e:
        logger.error(f"Error running diagnostics: {e}", exc_info=True)
        results["error"] = str(e)
    
    return results


def analyze_disconnect(close_code: int, close_reason: str) -> Dict[str, Any]:
    """Analyze a WebSocket disconnect event."""
    return WebSocketDiagnostics.analyze_disconnect_reason(close_code, close_reason)