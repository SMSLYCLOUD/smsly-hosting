"""Topology module."""
import re

from ..models import Service  # type: ignore[attr-defined]
from ..models.addons import Addon


class TopologyService:
    def build_graph(self):
        nodes = []
        links = []

        # 1. Services as Nodes
        services = Service.objects.prefetch_related('env_vars').only("id", "name")
        for svc in services:
            nodes.append({
                "id": f"svc-{svc.id}",
                "name": svc.name,
                "type": "SERVICE",
                "status": "ACTIVE"  # Should fetch from deployment
            })

        # 2. Addons as Nodes
        addons = Addon.objects.only("id", "name", "addon_type", "status", "connection_url")
        for addon in addons:
            nodes.append({
                "id": f"addon-{addon.id}",
                "name": addon.name,
                "type": addon.addon_type,
                "status": addon.status
            })

        # 3. Analyze Connections
        # Map connection strings to Addon IDs
        addon_map = {}
        for addon in addons:
            if addon.connection_url:
                addon_map[addon.connection_url] = f"addon-{addon.id}"

        # Analyze Service Env Vars
        for svc in services:
            svc_node_id = f"svc-{svc.id}"

            for var in svc.env_vars.all():
                value = var.value

                # A. Explicit Addon Links (via connection string match)
                if value in addon_map:
                    links.append({
                        "source": svc_node_id,
                        "target": addon_map[value],
                        "type": "DATABASE_CONNECTION"
                    })
                    continue

                # B. Heuristic: Internal Service Calls
                # Look for "http://other-service" or
                # "other-service.default.svc"
                for target_svc in services:
                    if target_svc.id == svc.id:
                        continue

                    # Regex to find service name in URL
                    # Matches http://my-api or my-api.default
                    if re.search(
                            f"https?://{target_svc.name}", value) or f"{target_svc.name}.default" in value:
                        links.append({
                            "source": svc_node_id,
                            "target": f"svc-{target_svc.id}",
                            "type": "HTTP_DEPENDENCY"
                        })
                        continue

                # C. Heuristic: Key Matching (e.g., REDIS_HOST = 'redis-addon')
                if "HOST" in var.key:
                    for addon in addons:
                        if addon.name in value:
                            links.append({
                                "source": svc_node_id,
                                "target": f"addon-{addon.id}",
                                "type": "CONFIG_DEPENDENCY"
                            })

        return {"nodes": nodes, "links": links}
