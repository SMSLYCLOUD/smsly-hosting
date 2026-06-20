import json
import logging
from typing import Any

from apps.intelligence.providers import _cached_ask

logger = logging.getLogger(__name__)

class EcosystemDeploymentSenate:
    @classmethod
    def propose_env_resolution(cls, graph) -> dict[str, Any] | None:
        if not hasattr(graph, 'services'):
            return None

        manifest_summary = {
            "mode": graph.manifest.get("mode", "production"),
            "services": {k: v.get("type", "unknown") for k, v in graph.services.items()},
            "addons": list(graph.addons.keys())
        }

        prompt = (
            "You are the CloudNeuron Ecosystem Senate.\n"
            "Analyze the following ecosystem manifest summary and propose environment configurations.\n"
            "Return valid JSON ONLY matching the following schema:\n"
            "{\n"
            "  \"resolutions\": {\n"
            "    \"service_key\": {\n"
            "      \"env_var_name\": \"suggested_value\"\n"
            "    }\n"
            "  }\n"
            "}\n"
            f"Manifest Summary: {json.dumps(manifest_summary)}\n"
        )

        try:
            response_text, _provider = _cached_ask(prompt)
            if response_text:
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    structured = json.loads(json_match.group(0))
                    if "resolutions" in structured and isinstance(structured["resolutions"], dict):
                        return structured
        except Exception as e:
            logger.error(f"Ecosystem Senate failed to propose resolution: {e}")
        return None
