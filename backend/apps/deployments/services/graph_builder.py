import logging
from typing import Any
from urllib.parse import urlparse

from ..models import Service  # type: ignore[attr-defined]
from ..models.addons import Addon

logger = logging.getLogger(__name__)

class GraphBuilder:
    """
    Builds a topology graph of services, addons, and their connections
    by inferring dependencies from environment variables and service metadata.
    """

    def __init__(self, user):
        self.user = user
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.service_map: dict[str, Service] = {}  # name -> Service
        self.addon_map: dict[str, Addon] = {}      # name -> Addon
        self.processed_ids: set[str] = set()

    def build(self) -> dict[str, list[dict[str, Any]]]:
        """Main entry point to build the graph."""
        services = Service.objects.filter(
            owner=self.user
        ).prefetch_related('addons', 'env_vars', 'deployments', 'primary_region')

        # 1. First pass: Register all known internal nodes (Services & Addons)
        for service in services:
            self.service_map[service.name] = service
            self._add_service_node(service)

            for addon in service.addons.all():
                # Map by name if present
                if addon.name:
                    self.addon_map[addon.name] = addon
                # Also map by ID for direct references if needed
                self.addon_map[str(addon.id)] = addon

                self._add_addon_node(addon, service)

        # 2. Second pass: Infer connections from Environment Variables
        for service in services:
            self._infer_connections(service)

        return {
            'nodes': self.nodes,
            'edges': self.edges
        }

    def _add_service_node(self, service: Service):
        """Creates a node for a compute service."""
        if str(service.id) in self.processed_ids:
            return

        latest_deploy = service.deployments.order_by('-created_at').first()
        status = latest_deploy.status if latest_deploy else 'UNKNOWN'

        # Determine kind/subtype
        kind = 'COMPUTE'
        subtype = service.deploy_type or 'GIT'

        node = {
            'id': str(service.id),
            'type': 'SERVICE', # Generic type for UI
            'data': {
                'label': service.name, # For UI display
                'name': service.name,
                'kind': kind,
                'subtype': subtype,
                'status': status,
                'region': service.primary_region.slug if service.primary_region else 'global',
                'url': service.public_domain,
                'metadata': {
                    'replicas': service.min_replicas,
                    'port': service.internal_port,
                    'language': service.buildpack,
                    'repo': service.repository_url,
                    'branch': service.branch,
                }
            }
        }
        self.nodes.append(node)
        self.processed_ids.add(str(service.id))

    def _add_addon_node(self, addon: Addon, parent_service: Service):
        """Creates a node for an addon (DB, Cache, etc)."""
        addon_id = f"addon-{addon.id}"
        if addon_id in self.processed_ids:
            return

        # Determine kind/subtype
        kind = 'DATABASE'
        subtype = (addon.addon_type or 'unknown').lower()

        if addon.addon_type in ['REDIS', 'MEMCACHED']:
            kind = 'CACHE'
        elif addon.addon_type in ['RABBITMQ', 'KAFKA', 'NATS']:
            kind = 'QUEUE'
        elif addon.addon_type in ['MINIO', 'S3']:
            kind = 'STORAGE'
        elif addon.addon_type in ['ELASTICSEARCH', 'QDRANT']:
            kind = 'SEARCH'

        node = {
            'id': addon_id,
            'type': 'ADDON',
            'data': {
                'label': addon.name or subtype,
                'name': addon.name or subtype,
                'kind': kind,
                'subtype': subtype,
                'status': addon.status,
                'parent_id': str(parent_service.id),
                'region': parent_service.primary_region.slug if parent_service.primary_region else 'global',
            }
        }
        self.nodes.append(node)
        self.processed_ids.add(addon_id)

        # Create explicit ownership edge (OWNER -> OWNED)
        self.edges.append({
            'id': f"owns-{parent_service.id}-{addon.id}",
            'source': str(parent_service.id),
            'target': addon_id,
            'type': 'OWNS',
            'label': 'owns'
        })

    def _infer_connections(self, service: Service):
        """Scans env vars to find connections to other services/addons."""

        known_names = set(self.service_map.keys()) | set(self.addon_map.keys())

        for env in service.env_vars.all():
            key = env.key.upper()
            val = env.value # Decrypted

            if not val or len(val) < 3:
                continue

            # 1. Try parsing as URL
            if '://' in val:
                try:
                    parsed = urlparse(val)
                    if parsed.hostname:
                        self._match_and_link(service, parsed.hostname, parsed.scheme, key)
                except ValueError:
                    pass

            # 2. Check for hostnames in comma-separated lists (e.g. KAFKA_BROKERS)
            if ',' in val and '://' not in val:
                parts = val.split(',')
                for part in parts:
                    # simplistic check: host:port or just host
                    candidate = part.split(':')[0].strip()
                    if candidate in known_names:
                        self._match_and_link(service, candidate, 'tcp', key)

            # 3. Direct match (e.g. DB_HOST=postgres-svc)
            if val in known_names:
                self._match_and_link(service, val, 'tcp', key)

            # 4. Heuristic substring match for known services
            # Only do this if key implies a host/url to avoid false positives in random config
            if '_HOST' in key or '_URL' in key or '_BROKER' in key:
                for candidate in known_names:
                    # Avoid matching "db" in "db-prod" if "db" is a service name (substring issue)
                    # We require word boundaries or exact match
                    if candidate == val or f"://{candidate}" in val or f"@{candidate}" in val:
                        self._match_and_link(service, candidate, 'custom', key)

    def _match_and_link(self, source_service: Service, target_name: str, protocol: str, env_key: str):
        """Creates an edge if target_name matches a known node."""
        target_id = None

        # Check Services
        if target_name in self.service_map:
            target_svc = self.service_map[target_name]
            # Don't link to self
            if target_svc.id == source_service.id:
                return
            target_id = str(target_svc.id)

        # Check Addons
        elif target_name in self.addon_map:
            target_addon = self.addon_map[target_name]
            target_id = f"addon-{target_addon.id}"

        if target_id:
            edge_id = f"{source_service.id}-{target_id}"

            # Check if edge already exists
            exists = any(e['id'] == edge_id for e in self.edges)

            if not exists:
                self.edges.append({
                    'id': edge_id,
                    'source': str(source_service.id),
                    'target': target_id,
                    'type': 'CONNECTS_TO',
                    'data': {
                        'protocol': protocol,
                        'evidence': env_key
                    }
                })
