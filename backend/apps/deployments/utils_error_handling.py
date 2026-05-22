"""
Standardized error handling utilities for the deployments app.

This module provides structured error responses and consistent error formatting
across all API endpoints and WebSocket consumers.
"""
import logging
from typing import Dict, Any, Optional, Union
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Standard error types for consistent error classification."""
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_ERROR = "authentication_error"
    AUTHORIZATION_ERROR = "authorization_error"
    NOT_FOUND = "not_found"
    RATE_LIMIT_ERROR = "rate_limit_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    INTERNAL_ERROR = "internal_error"
    BUSINESS_LOGIC_ERROR = "business_logic_error"
    PERMISSION_DENIED = "permission_denied"


class ErrorSeverity(Enum):
    """Error severity levels for logging and monitoring."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StandardizedError(Exception):
    """Base class for standardized errors."""
    
    def __init__(
        self,
        message: str,
        error_type: ErrorType,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        self.message = message
        self.error_type = error_type
        self.severity = severity
        self.details = details or {}
        self.user_message = user_message or message
        super().__init__(self.message)


class ValidationError(StandardizedError):
    """Validation error with structured details."""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        super().__init__(
            message=message,
            error_type=ErrorType.VALIDATION_ERROR,
            severity=ErrorSeverity.LOW,
            details=details or {},
            user_message=user_message
        )
        self.field = field


class AuthenticationError(StandardizedError):
    """Authentication error with specific context."""
    
    def __init__(
        self,
        message: str,
        token_info: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        super().__init__(
            message=message,
            error_type=ErrorType.AUTHENTICATION_ERROR,
            severity=ErrorSeverity.HIGH,
            details=details or {},
            user_message=user_message
        )
        self.token_info = token_info


class AuthorizationError(StandardizedError):
    """Authorization error with permission context."""
    
    def __init__(
        self,
        message: str,
        required_permission: Optional[str] = None,
        user_id: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        super().__init__(
            message=message,
            error_type=ErrorType.AUTHORIZATION_ERROR,
            severity=ErrorSeverity.HIGH,
            details=details or {},
            user_message=user_message
        )
        self.required_permission = required_permission
        self.user_id = user_id


class ExternalServiceError(StandardizedError):
    """Error when external services fail."""
    
    def __init__(
        self,
        message: str,
        service_name: str,
        service_error: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        super().__init__(
            message=message,
            error_type=ErrorType.EXTERNAL_SERVICE_ERROR,
            severity=ErrorSeverity.MEDIUM,
            details=details or {},
            user_message=user_message
        )
        self.service_name = service_name
        self.service_error = service_error
        self.status_code = status_code


def create_error_response(
    error: Union[StandardizedError, Exception],
    include_details: bool = False,
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a standardized error response dictionary.
    
    Args:
        error: The error to format
        include_details: Whether to include technical details
        request_id: Optional request ID for tracking
        
    Returns:
        Dict with standardized error structure
    """
    if isinstance(error, StandardizedError):
        response = {
            "error": {
                "type": error.error_type.value,
                "message": error.user_message,
                "severity": error.severity.value,
                "timestamp": None,  # Will be set by DRF
            },
            "request_id": request_id
        }
        
        if include_details and error.details:
            response["error"]["details"] = error.details
        
        return response
    else:
        # Handle generic exceptions
        logger.error(f"Unhandled exception: {error}", exc_info=True)
        return {
            "error": {
                "type": ErrorType.INTERNAL_ERROR.value,
                "message": "An unexpected error occurred",
                "severity": ErrorSeverity.HIGH.value,
                "timestamp": None,  # Will be set by DRF
            },
            "request_id": request_id
        }


def log_error(
    error: Union[StandardizedError, Exception],
    context: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log errors with structured context.
    
    Args:
        error: The error to log
        context: Additional context information
    """
    log_data = {
        "error_type": error.error_type.value if isinstance(error, StandardizedError) else "unknown",
        "severity": error.severity.value if isinstance(error, StandardizedError) else "high",
        "message": str(error),
    }
    
    if isinstance(error, StandardizedError) and error.details:
        log_data["details"] = error.details
    
    if context:
        log_data["context"] = context
    
    if isinstance(error, StandardizedError) and error.severity == ErrorSeverity.CRITICAL:
        logger.critical(f"Critical error: {log_data}")
    elif isinstance(error, StandardizedError) and error.severity == ErrorSeverity.HIGH:
        logger.error(f"High severity error: {log_data}")
    elif isinstance(error, StandardizedError) and error.severity == ErrorSeverity.MEDIUM:
        logger.warning(f"Medium severity error: {log_data}")
    else:
        logger.info(f"Low severity error: {log_data}")


def handle_api_error(func):
    """
    Decorator to handle API errors consistently.
    
    Wraps API view functions to catch and format errors consistently.
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except StandardizedError as e:
            log_error(e)
            from rest_framework.response import Response
            from rest_framework import status
            
            error_response = create_error_response(e)
            
            # Set appropriate HTTP status code based on error type
            if e.error_type == ErrorType.VALIDATION_ERROR:
                status_code = status.HTTP_400_BAD_REQUEST
            elif e.error_type == ErrorType.AUTHENTICATION_ERROR:
                status_code = status.HTTP_401_UNAUTHORIZED
            elif e.error_type == ErrorType.AUTHORIZATION_ERROR:
                status_code = status.HTTP_403_FORBIDDEN
            elif e.error_type == ErrorType.NOT_FOUND:
                status_code = status.HTTP_404_NOT_FOUND
            elif e.error_type == ErrorType.RATE_LIMIT_ERROR:
                status_code = status.HTTP_429_TOO_MANY_REQUESTS
            else:
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            
            return Response(error_response, status=status_code)
        except Exception as e:
            logger.exception(f"Unexpected error in API endpoint: {e}")
            from rest_framework.response import Response
            from rest_framework import status
            
            error_response = create_error_response(e)
            return Response(error_response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return wrapper