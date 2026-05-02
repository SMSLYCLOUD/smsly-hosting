import logging
from typing import Dict, Any, Optional
import json
from apps.intelligence.providers import ask_with_fallback

logger = logging.getLogger(__name__)

class EcosystemDeploymentSenate:
    @classmethod
    def propose_env_resolution(cls, graph) -> Optional[Dict[str, Any]]:
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
            response = ask_with_fallback([{"role": "system", "content": prompt}])
            if response:
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    structured = json.loads(json_match.group(0))
                    if "resolutions" in structured and isinstance(structured["resolutions"], dict):
                        return structured
        except Exception as e:
            logger.error(f"Ecosystem Senate failed to propose resolution: {e}")
        return None
