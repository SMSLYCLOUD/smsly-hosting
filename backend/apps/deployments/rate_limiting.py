"""
Rate limiting for SMSLY Hosting deployment endpoints.
Prevents abuse and ensures fair usage of build resources.
"""
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class DeploymentRateThrottle(UserRateThrottle):
    """
    Limits deployments to 10 per hour per user.
    Prevents resource exhaustion from excessive builds.
    """
    scope = 'deployments'
    rate = '10/hour'


class BurstRateThrottle(UserRateThrottle):
    """
    Burst protection - limits to 3 deployments per minute.
    Prevents rapid-fire deployment attempts.
    """
    scope = 'deployment_burst'
    rate = '3/minute'


class AIChatRateThrottle(UserRateThrottle):
    rate = '30/minute'
    scope = 'ai_chat'


class AIAnalysisRateThrottle(UserRateThrottle):
    rate = '10/minute'
    scope = 'ai_analysis'


class NodeTokenExchangeThrottle(AnonRateThrottle):
    """Rate-limit the anonymous node-token-exchange endpoint.

    The endpoint is AllowAny (the caller doesn't have a token yet),
    so this throttle is keyed by client IP. It exists to slow down
    brute-force credential attacks that target the admin account.
    """
    scope = 'node_token_exchange'
    rate = '5/minute'
