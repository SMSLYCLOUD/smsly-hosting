"""
Configuration settings for the service console monitoring system.
"""

# WebSocket connection settings
WEBSOCKET_IDLE_TIMEOUT = 1800  # 30 minutes (in seconds)
MAX_RECONNECT_ATTEMPTS = 10    # Maximum number of reconnection attempts
RECONNECT_BACKOFF_BASE = 1.5  # Base for exponential backoff
RECONNECT_JITTER_MS = 1000    # Random jitter in milliseconds

# Monitoring settings
ENABLE_DETAILED_LOGGING = True
LOG_CONNECTION_EVENTS = True
LOG_HEARTBEATS = False  # Set to True for verbose heartbeat logging
MONITOR_LATENCY = True
CONNECTION_ALERT_THRESHOLD = 3  # Alert after this many disconnects in a session

# Diagnostic settings
CAPTURE_NETWORK_STATS = True
CAPTURE_CLIENT_INFO = True
DIAGNOSTIC_MODE = False  # Set to True to enable verbose diagnostic logging

# Alert settings
ENABLE_ALERTS = True
ALERT_CHANNELS = ["log", "ui"]  # Options: log, ui, email, webhook
ALERT_COOLDOWN_SECONDS = 300  # Prevent alert spam

# Cloudflare-specific settings
CLOUDFLARE_WEBSOCKET_TIMEOUT = 300  # Cloudflare has a 5-minute timeout by default
OPTIMIZE_FOR_CLOUDFLARE = True  # Enables Cloudflare-specific optimizations
AUTO_APPLY_MONITORING = True  # Automatically apply monitoring when imported