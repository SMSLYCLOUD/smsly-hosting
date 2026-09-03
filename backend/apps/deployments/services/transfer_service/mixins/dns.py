import logging

import requests

from apps.deployments.models.core import PlatformConfig

logger = logging.getLogger(__name__)


class DnsMixin:
    @staticmethod
    def _resolve_zone_name(domain):
        """Resolve the registrable zone from a (possibly multi-label) domain.

        BUG: the old code passed config.domain (grid.smsly.cloud) directly
        as the Cloudflare zone name — but the zone is the parent (smsly.cloud).
        Querying zones?name=grid.smsly.cloud returns zero results and the
        entire DNS cutover silently fails for any platform domain with >2
        labels. Use the same naive suffix guess as apps.domains.services.dns.
        """
        parts = str(domain or '').strip().lower().rstrip('.').split('.')
        if len(parts) < 2:
            return str(domain or '').strip().lower()
        return '.'.join(parts[-2:])

    def _update_cloudflare_dns(self, domain, ip, token):
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"

        zone_name = self._resolve_zone_name(domain)
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': zone_name}, timeout=30)
        if not resp.ok:
            logger.warning("Transfer DNS cutover: zone lookup for %s returned %s", zone_name, resp.status_code)
            return

        zones = resp.json().get('result')
        if not zones:
            logger.warning("Transfer DNS cutover: zone %s not visible to this token", zone_name)
            return
        zone_id = zones[0]['id']

        records_to_update = ['@', '*']

        for name in records_to_update:
            search_name = f"{name}.{zone_name}" if name != '@' else zone_name
            resp = requests.get(f"{base_url}/zones/{zone_id}/dns_records",
                                headers=headers,
                                params={'type': 'A', 'name': search_name}, timeout=30)
            if resp.ok:
                results = resp.json().get('result', [])
                for record in results:
                    update_url = f"{base_url}/zones/{zone_id}/dns_records/{record['id']}"
                    payload = {
                        'type': 'A',
                        'name': record['name'],
                        'content': ip,
                        'ttl': record['ttl'],
                        'proxied': record['proxied']
                    }
                    requests.put(update_url, headers=headers, json=payload, timeout=30)

    def _update_service_a_record(self, public_domain, target_ip, token):
        config = PlatformConfig.load()
        platform_domain = config.domain
        if not platform_domain:
            return
        domain = str(public_domain or '').strip().lower()
        if not domain.endswith('.' + platform_domain):
            return
        name = domain[:-(len(platform_domain) + 1)]
        if not name:
            return
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"
        # FIX: resolve the parent zone, not the full platform domain
        # (grid.smsly.cloud is NOT a zone — smsly.cloud is)
        zone_name = self._resolve_zone_name(platform_domain)
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': zone_name}, timeout=30)
        if not resp.ok:
            logger.warning("Transfer DNS: zone lookup for %s returned %s", zone_name, resp.status_code)
            return
        zones = resp.json().get('result')
        if not zones:
            logger.warning("Transfer DNS: zone %s not visible to this token", zone_name)
            return
        zone_id = zones[0]['id']
        search = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params={'type': 'A', 'name': domain},
            timeout=30,
        )
        existing = search.json().get('result', []) if search.ok else []
        # Respect the Edge Shield proxy flag — don't silently un-proxy
        # a record that was intentionally orange-clouded
        desired_proxied = bool(getattr(config, 'edge_proxy_records', False))
        payload = {
            'type': 'A', 'name': name, 'content': target_ip, 'ttl': 1,
            'proxied': desired_proxied,
        }
        if existing:
            record_id = existing[0]['id']
            requests.put(
                f"{base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=headers, json=payload, timeout=30,
            )
        else:
            requests.post(
                f"{base_url}/zones/{zone_id}/dns_records",
                headers=headers, json=payload, timeout=30,
            )

    def _delete_service_a_record(self, public_domain, token):
        config = PlatformConfig.load()
        platform_domain = config.domain
        if not platform_domain:
            return
        domain = str(public_domain or '').strip().lower()
        if not domain.endswith('.' + platform_domain):
            return
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"
        zone_name = self._resolve_zone_name(platform_domain)
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': zone_name}, timeout=30)
        if not resp.ok:
            return
        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']
        search = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params={'type': 'A', 'name': domain},
            timeout=30,
        )
        if search.ok:
            for record in search.json().get('result', []):
                requests.delete(
                    f"{base_url}/zones/{zone_id}/dns_records/{record['id']}",
                    headers=headers, timeout=30,
                )
