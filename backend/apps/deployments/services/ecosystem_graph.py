from typing import Any

import yaml


def parse_ecosystem_manifest(manifest_content: str) -> dict[str, Any]:
    try:
        return yaml.safe_load(manifest_content) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid ecosystem manifest: {e}")

class EcosystemGraph:
    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.services = self.manifest.get("services", {})
        self.addons = self.manifest.get("addons", {})
        self.shared_env = self.manifest.get("shared_env", {}).get("groups", {})

    def get_service_dependencies(self, service_key: str) -> list[str]:
        service = self.services.get(service_key, {})
        return service.get("dependencies", [])

    def get_topological_order(self) -> list[str]:
        visited = set()
        temp_mark = set()
        order = []

        def visit(node: str):
            if node in temp_mark:
                raise ValueError(f"Circular dependency detected at node {node}")
            if node not in visited:
                temp_mark.add(node)
                for dep in self.get_service_dependencies(node):
                    if dep in self.services:
                        visit(dep)
                temp_mark.remove(node)
                visited.add(node)
                order.append(node)

        for service_key in self.services:
            if service_key not in visited:
                visit(service_key)

        return order

def build_ecosystem_graph(manifest_content: str) -> EcosystemGraph:
    if isinstance(manifest_content, str):
        parsed = parse_ecosystem_manifest(manifest_content)
        return EcosystemGraph(parsed)
    # Support passing a pre-parsed dict directly (legacy callers)
    if isinstance(manifest_content, dict):
        return EcosystemGraph(manifest_content)
    raise TypeError(f"Expected str or dict manifest, got {type(manifest_content).__name__}")
