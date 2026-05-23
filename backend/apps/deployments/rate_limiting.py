"""
Rate limiting for SMSLY Hosting deployment endpoints.
Prevents abuse and ensures fair usage of build resources.
"""
from rest_framework.throttling import UserRateThrottle


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
