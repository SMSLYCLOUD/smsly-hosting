import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _validate_ai_response_structure(response_text: str, expected_structure: str = "ecosystem_plan") -> bool:
    """
    Validate AI response structure before parsing to prevent unhashable type errors.
    Returns True if structure is valid, False otherwise.
    """
    # Basic text validation
    if not response_text or len(response_text.strip()) < 10:
        logger.warning("AI response is too short or empty")
        return False

    # Extract JSON from response
    try:
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
            logger.warning("No valid JSON structure found in AI response")
            return False

        json_str = response_text[start_idx:end_idx+1]
        data = json.loads(json_str)

        # Validate based on expected structure
        if expected_structure == "ecosystem_plan":
            # Must have services and addons as arrays
            if not isinstance(data.get("services"), list):
                logger.warning("AI response missing 'services' array")
                return False

            # Validate each service has required string fields
            for i, service in enumerate(data["services"]):
                if not isinstance(service, dict):
                    logger.warning(f"Service {i} is not a dict")
                    return False

                # Check for unhashable nested structures in critical fields
                for field in ["env_vars", "addons", "depends_on"]:
                    value = service.get(field)
                    if value is not None:
                        if not _validate_field_value(field, value):
                            logger.warning(f"Invalid data in service {i} field '{field}': {value}")
                            return False

        logger.info("AI response structure validation passed")
        return True

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse AI response JSON: {e}")
        return False
    except Exception as e:
        logger.warning(f"AI response validation failed: {e}")
        return False


def _validate_field_value(field_name: str, value: Any, depth: int = 0) -> bool:
    """
    Recursively validate a field value for unhashable types.
    Returns True if the value is safe for processing, False otherwise.
    """
    if depth > 10:  # Prevent infinite recursion
        logger.warning(f"Validation depth exceeded for field {field_name}")
        return False

    if value is None:
        return True

    # Safe atomic types
    if isinstance(value, (str, int, float, bool)):
        return True

    # Handle dictionaries - ensure all values are safe
    if isinstance(value, dict):
        for key, val in value.items():
            try:
                # Ensure keys are strings
                str_key = str(key)
                # Recursively validate values
                if not _validate_field_value(f"{field_name}.{str_key}", val, depth + 1):
                    return False
            except Exception as e:
                logger.warning(f"Error validating dict key {key} in field {field_name}: {e}")
                return False
        return True

    # Handle lists and tuples - ensure all items are safe
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            try:
                if not _validate_field_value(f"{field_name}[{i}]", item, depth + 1):
                    return False
            except Exception as e:
                logger.warning(f"Error validating list item {i} in field {field_name}: {e}")
                return False
        return True

    # Fallback - if we can't safely convert to string, it's problematic
    try:
        str(value)
        return True
    except Exception as e:
        logger.warning(f"Cannot convert value to string in field {field_name}: {e}")
        return False


def _sanitize_ai_response_for_processing(response_text: str) -> dict:
    """
    Sanitize AI response to ensure it's safe for processing by removing
    unhashable structures and converting to safe formats.
    """
    try:
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
            raise ValueError("No JSON found")

        json_str = response_text[start_idx:end_idx+1]
        data = json.loads(json_str)

        # Deep sanitize the response recursively
        return _deep_sanitize_data(data)

    except Exception as e:
        logger.warning(f"Failed to sanitize AI response: {e}")
        return {"services": [], "addons": [], "deploy_sequence": []}


def _deep_sanitize_data(data: Any) -> Any:
    """
    Recursively sanitize data to ensure all values are safe for processing.
    Converts all nested structures to string-based formats.
    """
    if data is None:
        return None

    # Handle atomic types
    if isinstance(data, (str, int, float, bool)):
        return data

    # Handle dictionaries - convert all keys and values to strings
    if isinstance(data, dict):
        sanitized_dict = {}
        for key, value in data.items():
            # Ensure keys are strings
            str_key = str(key)
            # Recursively sanitize values
            sanitized_value = _deep_sanitize_data(value)
            sanitized_dict[str_key] = sanitized_value
        return sanitized_dict

    # Handle lists and tuples - sanitize all items
    if isinstance(data, (list, tuple)):
        sanitized_list = []
        for item in data:
            sanitized_item = _deep_sanitize_data(item)
            if sanitized_item is not None:  # Skip None values
                sanitized_list.append(sanitized_item)
        return sanitized_list

    # Fallback - convert anything else to string
    try:
        return str(data)
    except Exception:
        logger.warning(f"Could not convert data to string: {data}")
        return ""
