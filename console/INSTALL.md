# Service Console Monitoring and Stability Improvements

This document provides instructions for installing and configuring the service console monitoring system and implementing stability improvements to fix disconnection issues.

## Installation

1. The monitoring system is already installed in the `console` directory.

2. Add the console directory to your Python path or install it as a package:

```bash
# From the smsly-hosting directory
pip install -e ./console
```

## Integration with Existing Backend

To integrate the monitoring system with your existing backend, add the following code to your Django app's `apps.py` file:

```python
from django.apps import AppConfig

class DeploymentsConfig(AppConfig):
    name = 'apps.deployments'
    
    def ready(self):
        # Import and apply monitoring to the TerminalConsumer
        from console.integration import apply_monitoring
        apply_monitoring()
```

Alternatively, you can manually apply the monitoring in your `consumers.py` file:

```python
from console.integration import patch_terminal_consumer

# After defining your TerminalConsumer class
TerminalConsumer = patch_terminal_consumer(TerminalConsumer)
```

## API Integration

To add the monitoring API endpoints to your Django REST Framework API, add the following to your `urls.py` file:

```python
from rest_framework.routers import DefaultRouter
from console.api import MonitoringViewSet

router = DefaultRouter()
router.register(r'monitoring', MonitoringViewSet, basename='monitoring')

urlpatterns = [
    # ... your existing URL patterns
    path('api/', include(router.urls)),
]
```

## Configuration

The monitoring system can be configured by modifying the values in `console/config.py`. The most important settings are:

- `WEBSOCKET_IDLE_TIMEOUT`: The idle timeout in seconds (default: 1800)
- `MAX_RECONNECT_ATTEMPTS`: Maximum number of reconnection attempts (default: 10)
- `ENABLE_DETAILED_LOGGING`: Whether to enable detailed logging (default: True)
- `DIAGNOSTIC_MODE`: Whether to enable diagnostic mode (default: False)

## Stability Improvements

The following stability improvements have been implemented:

### Frontend Changes (XtermConsole.tsx)

1. Increased heartbeat interval from 15 seconds to 30 seconds
2. Increased maximum reconnect attempts from 10 to 15
3. Increased maximum reconnect delay from 10 seconds to 30 seconds

### Backend Changes (consumers.py)

1. Increased idle timeout from 15 minutes to 30 minutes
2. Increased maximum exec reconnect attempts from 5 to 10
3. Decreased heartbeat wait timeout from 2.5 seconds to 1.5 seconds

### Proxy Configuration

A new Caddy configuration file has been created at `caddy-config/websocket-optimized.caddy` with optimized settings for WebSocket connections. To use this configuration:

1. Include it in your main Caddyfile
2. Adjust the domain and paths to match your environment
3. Reload Caddy to apply the changes

### Cloudflare Configuration

For Cloudflare-specific configuration recommendations, see `CLOUDFLARE_WEBSOCKET_CONFIG.md`.

## Usage

Once installed and integrated, the monitoring system will automatically track WebSocket connection events and provide diagnostic information when disconnects occur.

You can access the monitoring data through the API endpoints:

- `GET /api/monitoring/`: Get basic monitoring information
- `GET /api/monitoring/sessions/`: Get all session statistics
- `GET /api/monitoring/session/{deployment_id}:{user_id}/`: Get statistics for a specific session
- `GET /api/monitoring/events/`: Get recent connection events
- `GET /api/monitoring/patterns/`: Get disconnect patterns
- `POST /api/monitoring/run_diagnostics/`: Run diagnostics for a WebSocket URL
- `POST /api/monitoring/analyze_disconnect/`: Analyze a WebSocket disconnect event
- `POST /api/monitoring/update_config/`: Update monitoring configuration

## Troubleshooting

If you encounter issues with the monitoring system:

1. Check that the monitoring system is properly integrated with your TerminalConsumer class
2. Verify that the configuration settings are appropriate for your environment
3. Enable diagnostic mode by setting `DIAGNOSTIC_MODE = True` in `config.py`
4. Check the logs for any error messages from the monitoring system