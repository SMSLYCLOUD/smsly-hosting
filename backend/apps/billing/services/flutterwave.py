"""Flutterwave payment helpers (hosted checkout + webhook verification)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class FlutterwaveService:
    API_BASE = "https://api.flutterwave.com/v3"

    @staticmethod
    def is_configured() -> bool:
        key = getattr(settings, "FLUTTERWAVE_SECRET_KEY", "") or ""
        return bool(key.strip())

    @staticmethod
    def _secret_key() -> str:
        key = getattr(settings, "FLUTTERWAVE_SECRET_KEY", "") or ""
        key = key.strip()
        if not key:
            raise ValueError("Flutterwave is not configured (FLUTTERWAVE_SECRET_KEY missing).")
        return key

    @staticmethod
    def _webhook_secret_hash() -> str:
        # Flutterwave docs refer to this as "Secret Hash" (used in webhook verification).
        return (getattr(settings, "FLUTTERWAVE_WEBHOOK_SECRET_HASH", "") or "").strip()

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {FlutterwaveService._secret_key()}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def create_payment_link(
        *,
        user,
        tx_ref: str,
        amount: Decimal,
        currency: str,
        redirect_url: str,
        title: str = "Grid",
        description: str = "Grid plan upgrade",
        meta: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "tx_ref": tx_ref,
            "amount": str(amount),
            "currency": (currency or "USD").upper(),
            "redirect_url": redirect_url,
            "customer": {
                "email": getattr(user, "email", "") or None,
                "name": getattr(user, "username", "") or None,
            },
            "customizations": {
                "title": title,
                "description": description,
            },
        }
        if meta:
            payload["meta"] = meta

        url = f"{FlutterwaveService.API_BASE}/payments"
        resp = requests.post(url, headers=FlutterwaveService._headers(), json=payload, timeout=30)
        try:
            data = resp.json()
        except Exception:
            data = None

        if resp.status_code >= 400:
            raise ValueError(f"Flutterwave create payment failed ({resp.status_code}): {data or resp.text}")

        link = ((data or {}).get("data") or {}).get("link")
        if not link:
            raise ValueError("Flutterwave create payment did not return a checkout link.")
        return link

    @staticmethod
    def verify_webhook_signature(*, raw_body: bytes, headers: dict[str, str]) -> bool:
        """
        Flutterwave webhook verification.

        Flutterwave documentation has multiple header schemes in the wild.
        We accept either:
        - `verif-hash` header that must exactly match FLUTTERWAVE_WEBHOOK_SECRET_HASH
        - `flutterwave-signature` header that matches an HMAC-SHA256 over the raw body using the same secret hash
        """

        secret_hash = FlutterwaveService._webhook_secret_hash()
        if not secret_hash:
            # Fail-closed in production by default.
            if not getattr(settings, "DEBUG", False):
                raise ValueError("FLUTTERWAVE_WEBHOOK_SECRET_HASH is not configured.")
            return True

        verif_hash = (headers.get("verif-hash") or headers.get("Verif-Hash") or "").strip()
        if verif_hash:
            return hmac.compare_digest(verif_hash, secret_hash)

        signature = (headers.get("flutterwave-signature") or headers.get("Flutterwave-Signature") or "").strip()
        if not signature:
            return False

        digest = hmac.new(secret_hash.encode("utf-8"), raw_body, hashlib.sha256).digest()
        hex_sig = digest.hex()
        b64_sig = base64.b64encode(digest).decode("ascii")

        return hmac.compare_digest(signature, hex_sig) or hmac.compare_digest(signature, b64_sig)

    @staticmethod
    def parse_webhook(raw_body: bytes) -> dict[str, Any]:
        try:
            return json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            logger.error("Flutterwave webhook parse failed: %s", e)
            raise ValueError("Invalid JSON payload") from e
