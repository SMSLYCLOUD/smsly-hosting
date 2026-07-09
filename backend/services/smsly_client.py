# pylint:
"""Smsly Client module."""
# disable=line-too-long,broad-exception-caught,logging-fstring-interpolation,wrong-import-order
"""
SMSLY Platform Integration Client.

Provides native integration with SMSLY's SMS, Voice, and Verification services
for the hosting platform's internal use (alerts, notifications, 2FA).
"""
import logging
from typing import Any

import httpx
from decouple import config

logger = logging.getLogger(__name__)


class SMSLYClient:
    """
    Client for SMSLY Platform APIs.
    Used to send SMS alerts, voice calls, and verification codes.
    """

    def __init__(self):
        # Internal SMSLY Platform API endpoints
        self.sms_api_url = config(
            'SMSLY_SMS_API_URL',
            default='http://smsly-sms:8000/api/v1')
        self.voice_api_url = config(
            'SMSLY_VOICE_API_URL',
            default='http://smsly-voice:8000/api/v1')
        self.platform_api_url = config(
            'SMSLY_PLATFORM_API_URL',
            default='http://smsly-platform-api:8000/api/v1')

        # Internal service-to-service API key
        self.internal_api_key = config('SMSLY_INTERNAL_API_KEY', default='')

        self.headers = {
            'Authorization': f'Bearer {self.internal_api_key}',
            'Content-Type': 'application/json',
            'X-Service-Name': 'smsly-hosting'
        }

    async def send_sms(
        self,
        to_phone: str,
        message: str,
        sender_id: str = "SMSLY"
    ) -> dict[str, Any]:
        """
        Send SMS via SMSLY-SMS service.

        Args:
            to_phone: Recipient phone number (E.164 format)
            message: SMS message content
            sender_id: Sender ID / alphanumeric sender

        Returns:
            dict with message_id, status, cost
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.sms_api_url}/messages/send",
                    headers=self.headers,
                    json={
                        "to": to_phone,
                        "message": message,
                        "sender_id": sender_id,
                        "priority": "high"  # Alerts are high priority
                    }
                )
                response.raise_for_status()
                result = response.json()
                logger.info(
                    f"SMS sent to {to_phone[:6]}***: {result.get('message_id')}")
                return result
        except Exception as e:
            logger.error(f"Failed to send SMS: {e!s}")
            return {"error": str(e), "status": "failed"}

    async def send_voice_alert(
        self,
        to_phone: str,
        message: str,
        voice: str = "en-US-Neural2-F"
    ) -> dict[str, Any]:
        """
        Send voice call alert via SMSLY-VOICE service.
        Uses text-to-speech to deliver urgent alerts.

        Args:
            to_phone: Recipient phone number (E.164 format)
            message: Message to speak
            voice: TTS voice ID

        Returns:
            dict with call_id, status
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.voice_api_url}/calls/outbound",
                    headers=self.headers,
                    json={
                        "to": to_phone,
                        "tts_message": message,
                        "voice": voice,
                        "call_reason": "deployment_alert"
                    }
                )
                response.raise_for_status()
                result = response.json()
                logger.info(
                    f"Voice alert initiated to {to_phone[:6]}***: {result.get('call_id')}")
                return result
        except Exception as e:
            logger.error(f"Failed to send voice alert: {e!s}")
            return {"error": str(e), "status": "failed"}

    async def get_user_api_keys(self, user_id: str) -> dict[str, str]:
        """
        Fetch user's SMSLY API keys from Platform API.
        Used to auto-inject keys into deployed services.

        Args:
            user_id: User's unique identifier

        Returns:
            dict with api_key, api_secret
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.platform_api_url}/users/{user_id}/api-keys",
                    headers=self.headers
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch user API keys: {e!s}")
            return {}

    def analyze_logs_sync(self, logs: str) -> str:
        """
        Analyze build logs using Jules AI to find root causes and fixes.

        Args:
            logs: Build/Runtime logs

        Returns:
            str: AI diagnosis and fix suggestion
        """
        try:
            # Truncate logs if too long to avoid token limits
            truncated_logs = logs[-10000:] if len(logs) > 10000 else logs

            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.platform_api_url}/ai/analyze",
                    headers=self.headers,
                    json={
                        "logs": truncated_logs,
                        "context": "deployment_failure"
                    }
                )
                response.raise_for_status()
                return response.json().get("diagnosis", "No diagnosis returned.")
        except Exception as e:
            logger.error(f"Failed to analyze logs with Jules AI: {e!s}")
            return "AI Analysis failed. Please check logs manually."

    def send_sms_sync(
        self,
        to_phone: str,
        message: str,
        sender_id: str = "SMSLY"
    ) -> dict[str, Any]:
        """
        Synchronous SMS send for use in Celery tasks.
        """
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.sms_api_url}/messages/send",
                    headers=self.headers,
                    json={
                        "to": to_phone,
                        "message": message,
                        "sender_id": sender_id,
                        "priority": "high"
                    }
                )
                response.raise_for_status()
                result = response.json()
                logger.info(
                    f"SMS sent to {to_phone[:6]}***: {result.get('message_id')}")
                return result
        except Exception as e:
            logger.error(f"Failed to send SMS: {e!s}")
            return {"error": str(e), "status": "failed"}


# Singleton instance
smsly_client = SMSLYClient()
