import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

class CloudflareManager:
    """
    Manages CDN zones and DNS records via Cloudflare API.
    """

    def __init__(self):
        self.api_token = getattr(settings, 'CLOUDFLARE_API_TOKEN', None)
        self.base_url = "https://api.cloudflare.com/client/v4"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def provision_zone(self, domain_name):
        """
        Creates a new Zone in Cloudflare.
        """
        if not self.api_token:
            logger.warning("Cloudflare API Token not set. Skipping CDN provision.")
            return {"status": "skipped", "message": "No API Token"}

        try:
            payload = {
                "name": domain_name,
                "account": {"id": settings.CLOUDFLARE_ACCOUNT_ID},
                "jump_start": True
            }
            response = requests.post(f"{self.base_url}/zones", headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to provision Cloudflare zone: {e}")
            # Fallback / Simulation
            return {"status": "error", "message": str(e)}

    def add_dns_record(self, zone_id, record_type, name, content):
        """
        Adds a DNS record (A, CNAME) to the zone.
        """
        if not self.api_token:
            return

        payload = {
            "type": record_type,
            "name": name,
            "content": content,
            "proxied": True # Enable CDN
        }
        requests.post(f"{self.base_url}/zones/{zone_id}/dns_records", headers=self.headers, json=payload)
