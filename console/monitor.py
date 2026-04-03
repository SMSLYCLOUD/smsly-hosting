"""
Service console WebSocket connection monitoring system.

This module provides tools to monitor, log, and diagnose WebSocket connection
issues for the service console, helping to identify patterns in disconnects
and provide alerts when problems occur.
"""

import time
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

from . import config

# Configure logging
logger = logging.getLogger("console.monitor")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO if not config.DIAGNOSTIC_MODE else logging.DEBUG)

class ConnectionEvent:
    """Represents a WebSocket connection event."""
    
    def __init__(self, event_type: str, deployment_id: str, user_id: str, 
                 container_id: Optional[str] = None, details: Optional[Dict] = None):
        self.timestamp = datetime.now()
        self.event_type = event_type  # connect, disconnect, reconnect, error
        self.deployment_id = deployment_id
        self.user_id = user_id
        self.container_id = container_id
        self.details = details or {}
    
    def to_dict(self) -> Dict:
        """Convert event to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "deployment_id": self.deployment_id,
            "user_id": self.user_id,
            "container_id": self.container_id,
            "details": self.details
        }
    
    def __str__(self) -> str:
        return (f"ConnectionEvent({self.event_type}, deployment={self.deployment_id}, "
                f"user={self.user_id}, container={self.container_id})")


class SessionStats:
    """Tracks statistics for a WebSocket session."""
    
    def __init__(self, deployment_id: str, user_id: str):
        self.deployment_id = deployment_id
        self.user_id = user_id
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self.connect_count = 0
        self.disconnect_count = 0
        self.reconnect_count = 0
        self.error_count = 0
        self.last_activity = datetime.now()
        self.latency_samples: List[float] = []
        self.events: List[ConnectionEvent] = []
        self.is_active = True
        
    def add_event(self, event: ConnectionEvent) -> None:
        """Add a connection event to this session."""
        self.events.append(event)
        self.last_activity = datetime.now()
        
        if event.event_type == "connect":
            self.connect_count += 1
        elif event.event_type == "disconnect":
            self.disconnect_count += 1
        elif event.event_type == "reconnect":
            self.reconnect_count += 1
        elif event.event_type == "error":
            self.error_count += 1
    
    def add_latency_sample(self, latency_ms: float) -> None:
        """Add a latency measurement to this session."""
        self.latency_samples.append(latency_ms)
        self.last_activity = datetime.now()
    
    def close(self) -> None:
        """Mark this session as closed."""
        self.is_active = False
        self.end_time = datetime.now()
    
    def get_duration(self) -> timedelta:
        """Get the duration of this session."""
        end = self.end_time or datetime.now()
        return end - self.start_time
    
    def get_avg_latency(self) -> Optional[float]:
        """Get the average latency for this session."""
        if not self.latency_samples:
            return None
        return sum(self.latency_samples) / len(self.latency_samples)
    
    def get_disconnect_rate(self) -> float:
        """Get the rate of disconnects per minute."""
        duration_minutes = self.get_duration().total_seconds() / 60
        if duration_minutes < 0.1:  # Avoid division by near-zero
            return 0
        return self.disconnect_count / duration_minutes
    
    def to_dict(self) -> Dict:
        """Convert session stats to dictionary for serialization."""
        return {
            "deployment_id": self.deployment_id,
            "user_id": self.user_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.get_duration().total_seconds(),
            "connect_count": self.connect_count,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "error_count": self.error_count,
            "avg_latency_ms": self.get_avg_latency(),
            "disconnect_rate": self.get_disconnect_rate(),
            "is_active": self.is_active
        }


class ConsoleMonitor:
    """
    Monitors WebSocket connections for the service console.
    
    This class tracks connection events, analyzes patterns, and provides
    alerts when connection issues are detected.
    """
    
    def __init__(self):
        self.sessions: Dict[str, SessionStats] = {}  # deployment_id -> SessionStats
        self.events: List[ConnectionEvent] = []
        self.alert_history: List[Dict] = []
        self.last_alert_time: Dict[str, datetime] = {}  # alert_type -> last_time
        self._lock = threading.RLock()
        
        # Start background monitoring thread if enabled
        if config.ENABLE_ALERTS:
            self._monitor_thread = threading.Thread(
                target=self._background_monitor, 
                daemon=True
            )
            self._monitor_thread.start()
    
    def record_event(self, event_type: str, deployment_id: str, user_id: str,
                    container_id: Optional[str] = None, details: Optional[Dict] = None) -> None:
        """
        Record a WebSocket connection event.
        
        Args:
            event_type: Type of event (connect, disconnect, reconnect, error)
            deployment_id: ID of the deployment
            user_id: ID of the user
            container_id: Optional container ID
            details: Optional additional details about the event
        """
        with self._lock:
            event = ConnectionEvent(
                event_type=event_type,
                deployment_id=deployment_id,
                user_id=user_id,
                container_id=container_id,
                details=details
            )
            
            # Add to global event list
            self.events.append(event)
            
            # Log the event if enabled
            if config.LOG_CONNECTION_EVENTS:
                logger.info(f"Connection event: {event}")
            
            # Add to session stats
            session_key = f"{deployment_id}:{user_id}"
            if session_key not in self.sessions:
                self.sessions[session_key] = SessionStats(deployment_id, user_id)
            
            self.sessions[session_key].add_event(event)
            
            # Check for alert conditions
            if event_type == "disconnect" and config.ENABLE_ALERTS:
                self._check_disconnect_alerts(self.sessions[session_key])
    
    def record_latency(self, deployment_id: str, user_id: str, latency_ms: float) -> None:
        """Record a latency measurement for a session."""
        if not config.MONITOR_LATENCY:
            return
            
        with self._lock:
            session_key = f"{deployment_id}:{user_id}"
            if session_key not in self.sessions:
                self.sessions[session_key] = SessionStats(deployment_id, user_id)
            
            self.sessions[session_key].add_latency_sample(latency_ms)
    
    def close_session(self, deployment_id: str, user_id: str) -> None:
        """Mark a session as closed."""
        with self._lock:
            session_key = f"{deployment_id}:{user_id}"
            if session_key in self.sessions:
                self.sessions[session_key].close()
    
    def get_session_stats(self, deployment_id: str, user_id: str) -> Optional[Dict]:
        """Get statistics for a specific session."""
        with self._lock:
            session_key = f"{deployment_id}:{user_id}"
            if session_key in self.sessions:
                return self.sessions[session_key].to_dict()
            return None
    
    def get_all_session_stats(self) -> List[Dict]:
        """Get statistics for all sessions."""
        with self._lock:
            return [session.to_dict() for session in self.sessions.values()]
    
    def get_recent_events(self, count: int = 100) -> List[Dict]:
        """Get the most recent connection events."""
        with self._lock:
            return [event.to_dict() for event in self.events[-count:]]
    
    def get_disconnect_patterns(self) -> Dict[str, Any]:
        """
        Analyze disconnect patterns across all sessions.
        
        Returns a dictionary with various metrics and patterns related to disconnects.
        """
        with self._lock:
            all_sessions = list(self.sessions.values())
            if not all_sessions:
                return {"error": "No sessions recorded"}
            
            # Calculate basic stats
            total_sessions = len(all_sessions)
            sessions_with_disconnects = sum(1 for s in all_sessions if s.disconnect_count > 0)
            total_disconnects = sum(s.disconnect_count for s in all_sessions)
            total_reconnects = sum(s.reconnect_count for s in all_sessions)
            
            # Calculate time patterns
            disconnect_times = []
            for session in all_sessions:
                for event in session.events:
                    if event.event_type == "disconnect":
                        disconnect_times.append(event.timestamp)
            
            # Group by hour of day
            hour_counts = {}
            for dt in disconnect_times:
                hour = dt.hour
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
            
            # Calculate disconnect rates
            disconnect_rates = [s.get_disconnect_rate() for s in all_sessions 
                               if s.get_duration().total_seconds() > 60]  # At least 1 minute
            avg_disconnect_rate = (sum(disconnect_rates) / len(disconnect_rates)) 
                                  if disconnect_rates else 0
            
            return {
                "total_sessions": total_sessions,
                "sessions_with_disconnects": sessions_with_disconnects,
                "disconnect_percentage": (sessions_with_disconnects / total_sessions * 100) 
                                        if total_sessions > 0 else 0,
                "total_disconnects": total_disconnects,
                "total_reconnects": total_reconnects,
                "reconnect_success_rate": (total_reconnects / total_disconnects * 100) 
                                         if total_disconnects > 0 else 100,
                "avg_disconnect_rate": avg_disconnect_rate,
                "disconnect_by_hour": hour_counts,
                "timestamp": datetime.now().isoformat()
            }
    
    def _check_disconnect_alerts(self, session: SessionStats) -> None:
        """Check if alerts should be triggered based on disconnect patterns."""
        # Alert on multiple disconnects in a single session
        if session.disconnect_count >= config.CONNECTION_ALERT_THRESHOLD:
            alert_type = "multiple_disconnects"
            self._trigger_alert(
                alert_type=alert_type,
                message=f"Multiple disconnects detected for deployment {session.deployment_id}",
                level="warning",
                details={
                    "deployment_id": session.deployment_id,
                    "user_id": session.user_id,
                    "disconnect_count": session.disconnect_count,
                    "session_duration": session.get_duration().total_seconds()
                }
            )
    
    def _trigger_alert(self, alert_type: str, message: str, level: str = "warning", 
                      details: Optional[Dict] = None) -> None:
        """Trigger an alert through configured channels."""
        # Check cooldown period
        now = datetime.now()
        if (alert_type in self.last_alert_time and 
            (now - self.last_alert_time[alert_type]).total_seconds() < config.ALERT_COOLDOWN_SECONDS):
            return  # Still in cooldown period
        
        self.last_alert_time[alert_type] = now
        
        alert = {
            "type": alert_type,
            "message": message,
            "level": level,
            "timestamp": now.isoformat(),
            "details": details or {}
        }
        
        self.alert_history.append(alert)
        
        # Log alert
        if "log" in config.ALERT_CHANNELS:
            log_method = getattr(logger, level, logger.warning)
            log_method(f"ALERT: {message}")
        
        # Other alert channels would be implemented here
        # - UI alerts
        # - Email notifications
        # - Webhook calls
    
    def _background_monitor(self) -> None:
        """Background thread to monitor for issues and trigger alerts."""
        while True:
            try:
                # Sleep first to avoid immediate alerts on startup
                time.sleep(60)  # Check every minute
                
                with self._lock:
                    # Check for inactive sessions that weren't properly closed
                    now = datetime.now()
                    for session_key, session in list(self.sessions.items()):
                        if (session.is_active and 
                            (now - session.last_activity).total_seconds() > config.WEBSOCKET_IDLE_TIMEOUT):
                            # Session appears abandoned
                            logger.warning(f"Session {session_key} appears abandoned, marking as closed")
                            session.close()
                    
                    # Analyze global patterns periodically
                    patterns = self.get_disconnect_patterns()
                    if patterns.get("disconnect_percentage", 0) > 50:
                        # More than half of sessions have disconnects
                        self._trigger_alert(
                            alert_type="high_disconnect_rate",
                            message="High percentage of sessions experiencing disconnects",
                            level="error",
                            details=patterns
                        )
            
            except Exception as e:
                logger.error(f"Error in background monitoring: {e}", exc_info=True)


# Global monitor instance
monitor = ConsoleMonitor()


def get_monitor() -> ConsoleMonitor:
    """Get the global monitor instance."""
    return monitor