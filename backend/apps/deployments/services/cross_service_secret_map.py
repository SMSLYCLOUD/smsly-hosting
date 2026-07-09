"""Cross-service secret trust map builder.

Reads SECRETS-MANIFEST.yaml files from all services in an ecosystem and
builds a complete trust graph so that paired secrets get the same value.

Only produces secrets for ACTUALLY PAIRED services — not imaginary ones.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def load_secrets_manifest(source_dir: str) -> dict[str, Any]:
    """Load a single service's SECRETS-MANIFEST.yaml."""
    import yaml

    candidates = [
        os.path.join(source_dir, "SECRETS-MANIFEST.yaml"),
        os.path.join(source_dir, "SECRETS-MANIFEST.yml"),
    ]

    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("Failed to parse SECRETS-MANIFEST at %s: %s", path, e)
    return {"serves_as": [], "expects_from": []}


def _parse_serves_as(entry: Any) -> list[dict[str, str]]:
    """Normalize serves_as entries from various formats."""
    results = []
    if isinstance(entry, list):
        for item in entry:
            if isinstance(item, dict):
                for local_var, mapping in item.items():
                    # mapping can be "service (remote_var)" or just "service"
                    parts = str(mapping).split("(")
                    target_service = parts[0].strip()
                    remote_var = ""
                    if len(parts) > 1:
                        remote_var = parts[1].rstrip(")").strip()
                    results.append(
                        {
                            "local_var": local_var,
                            "target_service": target_service,
                            "remote_var": remote_var,
                        }
                    )
            elif isinstance(item, str) and "→" in item:
                parts = item.split("→")
                local_var = parts[0].strip()
                rest = parts[1].strip()
                rp = rest.split("(")
                target_service = rp[0].strip()
                remote_var = rp[1].rstrip(")").strip() if len(rp) > 1 else ""
                results.append(
                    {
                        "local_var": local_var,
                        "target_service": target_service,
                        "remote_var": remote_var,
                    }
                )
    return results


def _parse_expects_from(entry: Any) -> list[dict[str, str]]:
    """Normalize expects_from entries."""
    results = []
    if isinstance(entry, list):
        for item in entry:
            if isinstance(item, dict):
                for local_var, mapping in item.items():
                    parts = str(mapping).split("(")
                    source_service = parts[0].strip()
                    remote_var = ""
                    if len(parts) > 1:
                        remote_var = parts[1].rstrip(")").strip()
                    results.append(
                        {
                            "local_var": local_var,
                            "source_service": source_service,
                            "remote_var": remote_var,
                        }
                    )
            elif isinstance(item, str) and "→" in item:
                parts = item.split("→")
                local_var = parts[0].strip()
                rest = parts[1].strip()
                rp = rest.split("(")
                source_service = rp[0].strip()
                remote_var = rp[1].rstrip(")").strip() if len(rp) > 1 else ""
                results.append(
                    {
                        "local_var": local_var,
                        "source_service": source_service,
                        "remote_var": remote_var,
                    }
                )
    return results


def build_cross_service_map(services_with_source: dict[str, str]) -> dict[str, Any]:
    """Build cross-service secret map from all services' SECRETS-MANIFEST.yaml files.

    Args:
        services_with_source: dict of service_name -> source_dir path

    Returns:
        {
            "pairs": [
                {
                    "secret_name": "GATEWAY_SECRET",
                    "sender": "smsly-security-gateway",
                    "sender_var": "GATEWAY_TO_PLATFORM_SECRET",
                    "receiver": "smsly-platform-api",
                    "receiver_var": "GATEWAY_SECRET",
                    "value": "<generated>"
                }
            ],
            "service_map": {
                "smsly-platform-api": {
                    "expects": {"GATEWAY_SECRET": {"from": "smsly-security-gateway", "remote_var": "GATEWAY_TO_PLATFORM_SECRET"}},
                    "serves": {"PLATFORM_TO_IDENTITY_SECRET": {"to": "smsly-identity-service", "remote_var": "PLATFORM_API_SECRET"}}
                },
                ...
            }
        }
    """
    manifests: dict[str, dict[str, Any]] = {}
    for svc_name, src_dir in services_with_source.items():
        manifest = load_secrets_manifest(src_dir)
        if manifest:
            manifests[svc_name] = manifest

    # Parse all serves_as and expects_from entries
    all_serves: dict[str, list[dict[str, str]]] = {}
    all_expects: dict[str, list[dict[str, str]]] = {}

    for svc_name, manifest in manifests.items():
        all_serves[svc_name] = _parse_serves_as(manifest.get("serves_as", []))
        all_expects[svc_name] = _parse_expects_from(manifest.get("expects_from", []))

    # Build the reverse lookup: which service serves a given (target_service, remote_var)?
    serves_lookup: dict[str, list[dict[str, str]]] = {}
    for svc_name, serves_list in all_serves.items():
        for entry in serves_list:
            key = f"{entry['target_service']}:{entry['remote_var']}"
            serves_lookup.setdefault(key, []).append({**entry, "service": svc_name})

    # Build the pairs
    pairs: list[dict[str, str]] = []
    # Track already-paired secrets to avoid duplicates
    paired_set: set[str] = set()

    for svc_name, expects_list in all_expects.items():
        for entry in expects_list:
            local_var = entry["local_var"]
            entry["source_service"]
            remote_var = entry["remote_var"]

            # Look up who serves this pair
            lookup_key = f"{svc_name}:{local_var}"
            serving_entries = serves_lookup.get(lookup_key, [])

            # Also try reverse: who serves the remote_var to this service
            if not serving_entries and remote_var:
                lookup_key2 = f"{svc_name}:{remote_var}"
                serving_entries = serves_lookup.get(lookup_key2, [])

            for serve_entry in serving_entries:
                pair_key = f"{serve_entry['service']}:{serve_entry['local_var']}<->{svc_name}:{local_var}"
                if pair_key in paired_set:
                    continue
                paired_set.add(pair_key)

                pairs.append(
                    {
                        "secret_name": local_var,
                        "sender": serve_entry["service"],
                        "sender_var": serve_entry["local_var"],
                        "receiver": svc_name,
                        "receiver_var": local_var,
                        "value": "",  # To be filled during resolution
                    }
                )

            # If no serves entry found, this is a self-issued secret
            if not serving_entries:
                pair_key = f"{svc_name}:{local_var}__self"
                if pair_key not in paired_set:
                    paired_set.add(pair_key)
                    pairs.append(
                        {
                            "secret_name": local_var,
                            "sender": svc_name,
                            "sender_var": local_var,
                            "receiver": svc_name,
                            "receiver_var": local_var,
                            "value": "",
                        }
                    )

    # Build service_map for quick lookups
    service_map: dict[str, dict[str, Any]] = {}
    for svc_name in manifests:
        expects: dict[str, dict[str, str]] = {}
        serves: dict[str, dict[str, str]] = {}

        for entry in all_expects.get(svc_name, []):
            expects[entry["local_var"]] = {
                "from": entry["source_service"],
                "remote_var": entry["remote_var"],
            }

        for entry in all_serves.get(svc_name, []):
            serves[entry["local_var"]] = {
                "to": entry["target_service"],
                "remote_var": entry["remote_var"],
            }

        service_map[svc_name] = {"expects": expects, "serves": serves}

    return {"pairs": pairs, "service_map": service_map}


def generate_secrets_for_map(
    secret_map: dict[str, Any],
) -> dict[str, Any]:
    """Generate strong secret values for all unpaired secrets in the map.

    Modifies pairs in-place and returns the updated map.
    """
    from .manifest_env_resolver import generate_strong_secret

    for pair in secret_map["pairs"]:
        if not pair["value"]:
            pair["value"] = generate_strong_secret(48)

    return secret_map


def get_secret_for_service(
    secret_map: dict[str, Any],
    service_name: str,
    local_var: str,
) -> str | None:
    """Look up the value of a secret for a given service and var name.

    This is used by ManifestEnvResolver to fill in expected secrets.
    """
    for pair in secret_map.get("pairs", []):
        if pair["receiver"] == service_name and pair["receiver_var"] == local_var:
            return pair["value"]
        if pair["sender"] == service_name and pair["sender_var"] == local_var:
            return pair["value"]
    return None
