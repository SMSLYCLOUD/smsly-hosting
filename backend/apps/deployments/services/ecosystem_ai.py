import logging
from typing import Dict, Any, Optional
import json
import re
from pydantic import BaseModel, Field, ValidationError
from apps.intelligence.providers import ask_with_fallback

logger = logging.getLogger(__name__)

class EcosystemResolutionSchema(BaseModel):
    resolutions: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="A mapping of service keys to their environment variable resolutions."
    )

class EcosystemDeploymentSenate:
    @classmethod
    def _extract_json(cls, response: str) -> str:
        """Extracts JSON from markdown fences or mixed prose."""
        if not response:
            return ""
        # Try finding markdown JSON fences
        fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if fence_match:
            return fence_match.group(1)
        # Try finding raw JSON object
        json_match = re.search(r'(\{.*\})', response, re.DOTALL)
        if json_match:
            return json_match.group(1)
        return response

    @classmethod
    def _repair_json(cls, text: str) -> str:
        """Applies basic heuristics to repair malformed JSON."""
        # Remove trailing commas
        text = re.sub(r',\s*([\}\]])', r'\1', text)
        # Fix missing quotes around keys (basic approximation)
        text = re.sub(r'([{,]\s*)([a-zA-Z0-9_]+)(\s*:)', r'\1"\2"\3', text)
        # Replace python True/False/None with JSON equivalents
        text = text.replace(": True", ": true").replace(": False", ": false").replace(": None", ": null")
        return text

    @classmethod
    def _normalize_dict_types(cls, data: Any) -> Any:
        """Recursively normalize dict values to strings to satisfy the schema."""
        if isinstance(data, dict):
            return {k: cls._normalize_dict_types(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls._normalize_dict_types(v) for v in data]
        if data is None:
            return ""
        return str(data)

    @classmethod
    def _fallback_inference(cls, graph) -> Dict[str, Any]:
        """Deterministic heuristic fallback if AI fails."""
        resolutions = {}
        for service_key, service in graph.services.items():
            res = {}
            for env_key, config in service.get("env", {}).items():
                if config.get("source") == "external_required":
                    res[env_key] = f"fallback_{env_key}"
            if res:
                resolutions[service_key] = res
        return {"resolutions": resolutions}

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

        max_retries = 2
        last_error = None
        raw_responses = []
        for attempt in range(max_retries):
            try:
                response = ask_with_fallback([{"role": "system", "content": prompt}])
                if not response:
                    last_error = "Empty AI response"
                    continue
                raw_responses.append(response)

                raw_json = cls._extract_json(response)
                try:
                    parsed = json.loads(raw_json)
                except json.JSONDecodeError:
                    repaired = cls._repair_json(raw_json)
                    try:
                        parsed = json.loads(repaired)
                    except json.JSONDecodeError as e:
                        last_error = f"JSON Parse Error: {str(e)}"
                        logger.warning(f"Failed to parse JSON on attempt {attempt+1}: {e}")
                        continue

                # Validation and Normalization
                parsed = cls._normalize_dict_types(parsed)
                try:
                    validated = EcosystemResolutionSchema(**parsed)

                    # Add an explicit rejection if the critical field resolutions is empty
                    if not validated.resolutions and attempt < max_retries - 1:
                        last_error = "Validation Error: resolutions field is empty"
                        logger.warning(f"Schema valid but empty resolutions on attempt {attempt+1}")
                        prompt += "\nResponse was empty. Please provide populated resolutions."
                        continue

                    return validated.model_dump()
                except ValidationError as ve:
                    last_error = f"Schema Validation Error: {str(ve)}"
                    logger.warning(f"Schema validation failed on attempt {attempt+1}: {ve}")
                    # Update prompt for retry
                    prompt += f"\nValidation Error on previous attempt: {ve}. Please fix."
                    continue

            except Exception as e:
                last_error = f"Unexpected Error: {str(e)}"
                logger.error(f"Ecosystem Senate encountered error: {e}")

        logger.error(f"AI resolution failed after all retries. Last error: {last_error}. Preserved Raw Responses: {json.dumps(raw_responses)}")
        logger.error("Falling back to deterministic heuristic inference.")

        # Actionable diagnostic information preserved via logger for transparency and failure fast behavior.
        # Fallback is deterministic, but if needed, calling code can see these logs.
        return cls._fallback_inference(graph)
