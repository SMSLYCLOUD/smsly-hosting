import json
import re


def _yaml_scalar(value) -> str:
    return json.dumps(str(value))


def _bounded_error(exc, limit=2000) -> str:
    safe = str(exc).replace("\x00", "")
    safe = re.sub(r'[A-Za-z0-9\-_=+/]{20,}', '***', safe)
    return safe[:limit]
