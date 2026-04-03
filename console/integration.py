"""
Integration module for connecting the monitoring system with the backend.

This module provides functions and decorators to integrate the monitoring
system with the existing Django Channels WebSocket consumer.
"""

import functools
import inspect
import logging
import time
from typing import Any, Callable, Dict, Optional, Type, TypeVar, cast

from . import config, monitor, diagnostics

logger = logging.getLogger("console.integration")

# Type variable for the decorator
F = TypeVar('F', bound=Callable[..., Any])

def monitor_connection(event_type: str) -> Callable[[F], F]:
    """
    Decorator to monitor WebSocket connection events.
    
    This decorator can be applied to methods in the TerminalConsumer class
    to automatically record connection events.
    
    Args:
        event_type: The type of event to record (connect, disconnect, reconnect, error)
    
    Returns:
        A decorator function
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Extract relevant information from the consumer instance
            deployment_id = getattr(self, 'deployment_id', None)
            user_id = getattr(self, 'user', None)
            if hasattr(user_id, 'id'):
                user_id = user_id.id
            container_id = getattr(self, 'container_id', None)
            
            # Prepare details dictionary
            details = {}
            
            # For disconnect events, capture close code if available
            if event_type == "disconnect" and args:
                details["close_code"] = args[0]
                
                # Analyze disconnect reason
                if details["close_code"]:
                    analysis = diagnostics.analyze_disconnect(
                        details["close_code"], 
                        "Unknown reason"  # We don't have the reason text
                    )
                    details["analysis"] = analysis
            
            # For error events, capture exception details
            if event_type == "error" and args:
                exception = args[0]
                details["error"] = str(exception)
                details["error_type"] = exception.__class__.__name__
            
            # Record the event before executing the function
            if deployment_id and user_id:
                monitor.get_monitor().record_event(
                    event_type=event_type,
                    deployment_id=deployment_id,
                    user_id=user_id,
                    container_id=container_id,
                    details=details
                )
            
            # Execute the original function
            start_time = time.time()
            result = await func(self, *args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000
            
            # For connect events, record latency
            if event_type == "connect" and deployment_id and user_id:
                monitor.get_monitor().record_latency(
                    deployment_id=deployment_id,
                    user_id=user_id,
                    latency_ms=duration_ms
                )
            
            return result
        
        return cast(F, wrapper)
    
    return decorator


def monitor_message(func: F) -> F:
    """
    Decorator to monitor WebSocket message handling.
    
    This decorator can be applied to the receive method in the TerminalConsumer
    class to monitor message handling and latency.
    """
    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Extract relevant information from the consumer instance
        deployment_id = getattr(self, 'deployment_id', None)
        user_id = getattr(self, 'user', None)
        if hasattr(user_id, 'id'):
            user_id = user_id.id
        
        # Execute the original function and measure latency
        start_time = time.time()
        result = await func(self, *args, **kwargs)
        duration_ms = (time.time() - start_time) * 1000
        
        # Record latency for the message handling
        if deployment_id and user_id and config.MONITOR_LATENCY:
            monitor.get_monitor().record_latency(
                deployment_id=deployment_id,
                user_id=user_id,
                latency_ms=duration_ms
            )
        
        return result
    
    return cast(F, wrapper)


def run_diagnostics_for_disconnect(deployment_id: str, user_id: str, 
                                  close_code: int, user_agent: Optional[str] = None) -> None:
    """
    Run diagnostics when a disconnect occurs.
    
    Args:
        deployment_id: The ID of the deployment
        user_id: The ID of the user
        close_code: The WebSocket close code
        user_agent: Optional user agent string
    """
    if not config.DIAGNOSTIC_MODE:
        return
    
    # Construct WebSocket URL (this is a placeholder - in a real implementation,
    # you would need to reconstruct the actual URL used by the client)
    ws_url = f"wss://example.com/ws/terminal/{deployment_id}/"
    
    # Run diagnostics
    diagnostic_results = diagnostics.run_diagnostics(ws_url, user_agent)
    
    # Log the results
    logger.info(f"Diagnostic results for disconnect (deployment={deployment_id}, "
               f"user={user_id}, code={close_code}):\n{diagnostic_results}")
    
    # In a real implementation, you might store these results in a database
    # or send them to a monitoring system


def patch_terminal_consumer(consumer_class: Type) -> Type:
    """
    Patch the TerminalConsumer class to add monitoring.
    
    This function monkey-patches the TerminalConsumer class to add
    monitoring to its methods.
    
    Args:
        consumer_class: The TerminalConsumer class to patch
    
    Returns:
        The patched class
    """
    # Store original methods
    original_connect = consumer_class.connect
    original_disconnect = consumer_class.disconnect
    original_receive = consumer_class.receive
    
    # Replace with monitored versions
    consumer_class.connect = monitor_connection("connect")(original_connect)
    consumer_class.disconnect = monitor_connection("disconnect")(original_disconnect)
    consumer_class.receive = monitor_message(original_receive)
    
    # Add a method to run diagnostics on disconnect
    original_disconnect = consumer_class.disconnect
    
    @functools.wraps(original_disconnect)
    async def monitored_disconnect(self: Any, close_code: int) -> Any:
        # Run diagnostics before disconnecting
        user_id = getattr(self, 'user', None)
        if hasattr(user_id, 'id'):
            user_id = user_id.id
        
        if config.DIAGNOSTIC_MODE and self.deployment_id and user_id:
            # Get user agent from scope if available
            user_agent = None
            if hasattr(self, 'scope') and isinstance(self.scope, dict):
                headers = self.scope.get('headers', [])
                for name, value in headers:
                    if name == b'user-agent':
                        user_agent = value.decode('utf-8')
                        break
            
            run_diagnostics_for_disconnect(
                deployment_id=self.deployment_id,
                user_id=user_id,
                close_code=close_code,
                user_agent=user_agent
            )
        
        # Call original disconnect method
        return await original_disconnect(self, close_code)
    
    consumer_class.disconnect = monitored_disconnect
    
    return consumer_class


def apply_monitoring(module_name: str = 'apps.deployments.consumers', 
                    class_name: str = 'TerminalConsumer') -> bool:
    """
    Apply monitoring to the TerminalConsumer class.
    
    This function imports the TerminalConsumer class and applies
    monitoring to it.
    
    Args:
        module_name: The name of the module containing the TerminalConsumer class
        class_name: The name of the TerminalConsumer class
    
    Returns:
        True if monitoring was successfully applied, False otherwise
    """
    try:
        # Import the module
        import importlib
        module = importlib.import_module(module_name)
        
        # Get the class
        consumer_class = getattr(module, class_name)
        
        # Patch the class
        patch_terminal_consumer(consumer_class)
        
        logger.info(f"Successfully applied monitoring to {module_name}.{class_name}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to apply monitoring: {e}", exc_info=True)
        return False