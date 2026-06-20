"""Cryptomus payment helpers (invoice creation + webhook verification)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class CryptomusService:
    API_BASE = "https://api.cryptomus.com/v1"

    @staticmethod
    def is_configured() -> bool:
        merchant = (getattr(settings, "CRYPTOMUS_MERCHANT_ID", "") or "").strip()
        api_key = (getattr(settings, "CRYPTOMUS_API_KEY", "") or "").strip()
        return bool(merchant and api_key)

    @staticmethod
    def _merchant_id() -> str:
        merchant = (getattr(settings, "CRYPTOMUS_MERCHANT_ID", "") or "").strip()
        if not merchant:
            raise ValueError("Cryptomus is not configured (CRYPTOMUS_MERCHANT_ID missing).")
        return merchant

    @staticmethod
    def _api_key() -> str:
        api_key = (getattr(settings, "CRYPTOMUS_API_KEY", "") or "").strip()
        if not api_key:
            raise ValueError("Cryptomus is not configured (CRYPTOMUS_API_KEY missing).")
        return api_key

    @staticmethod
    def _json_b64(payload: dict[str, Any]) -> str:
        # Cryptomus signs base64(json) + api_key (md5).
        # Use compact separators; preserve key insertion order.
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    @staticmethod
    def sign(payload: dict[str, Any]) -> str:
        base = CryptomusService._json_b64(payload) + CryptomusService._api_key()
        return hashlib.md5(base.encode("utf-8")).hexdigest()

    @staticmethod
    def _headers(payload: dict[str, Any]) -> dict[str, str]:
        return {
            "merchant": CryptomusService._merchant_id(),
            "sign": CryptomusService.sign(payload),
            "Content-Type": "application/json",
        }

    @staticmethod
    def create_invoice(
        *,
        order_id: str,
        amount: Decimal,
        currency: str,
        url_return: str,
        url_callback: str,
        lifetime_seconds: int = 3600,
        additional_data: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "order_id": order_id,
            "amount": str(amount),
            "currency": (currency or "USD").upper(),
            "url_return": url_return,
            "url_callback": url_callback,
            "lifetime": lifetime_seconds,
        }
        if additional_data:
            payload["additional_data"] = additional_data

        url = f"{CryptomusService.API_BASE}/payment"
        resp = requests.post(url, headers=CryptomusService._headers(payload), json=payload, timeout=30)
        try:
            data = resp.json()
        except Exception:
            data = None

        if resp.status_code >= 400:
            raise ValueError(f"Cryptomus create invoice failed ({resp.status_code}): {data or resp.text}")

        # Docs vary between `result` and `data`. Accept both.
        result = (data or {}).get("result") or (data or {}).get("data") or {}
        pay_url = result.get("url") or result.get("payment_url") or result.get("invoice_url")
        if not pay_url:
            raise ValueError("Cryptomus create invoice did not return a payment URL.")
        return pay_url

    @staticmethod
    def verify_webhook(*, payload: dict[str, Any]) -> bool:
        """
        Cryptomus webhook verification.

        Webhooks include a `sign` field inside the JSON body. Verification:
        - remove `sign`
        - compute md5(base64(json_without_sign) + api_key)
        """
        incoming = (payload.get("sign") or "").strip()
        if not incoming:
            return False

        unsigned = {k: v for k, v in payload.items() if k != "sign"}
        expected = CryptomusService.sign(unsigned)
        return incoming.lower() == expected.lower()
