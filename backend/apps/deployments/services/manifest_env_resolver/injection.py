import logging
import os
import re

from .constants import STACK_DEFAULTS, _HEURISTIC_DEFAULTS, generate_strong_secret

logger = logging.getLogger(__name__)


class InjectionMixin:
    def _inject_stack_defaults(self, resolved: dict[str, str]) -> None:
        defaults = STACK_DEFAULTS.get(self.stack, {})
        for key, val in defaults.items():
            if key not in resolved and val is not None and val != "{{GENERATED}}":
                resolved[key] = val
        if self.service_name and "SERVICE_NAME" not in resolved:
            resolved["SERVICE_NAME"] = self.service_name
        if self.service_name and "OTEL_SERVICE_NAME" not in resolved:
            resolved["OTEL_SERVICE_NAME"] = self.service_name

    def _get_stack_default(self, var_name: str) -> str | None:
        defaults = STACK_DEFAULTS.get(self.stack, {})
        val = defaults.get(var_name)
        if val is None:
            if var_name == "DJANGO_SETTINGS_MODULE" and self.stack == "django":
                return self._detect_django_settings_module()
            return None
        return val

    def _detect_django_settings_module(self) -> str:
        if not self.source_dir:
            return "config.settings"
        for fname in ("manage.py", "app.py", "main.py"):
            path = os.path.join(self.source_dir, fname)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m = re.search(
                    r'DJANGO_SETTINGS_MODULE[,\s]*["\']([^"\']+)["\']', content
                )
                if m:
                    return m.group(1)
            except OSError:
                pass
        return "config.settings"

    def _get_heuristic_default(self, var_name: str) -> str | None:
        val = _HEURISTIC_DEFAULTS.get(var_name)
        if val == "{{GENERATED}}":
            return generate_strong_secret(48)
        if val == f"{{{{SERVICE:{self.service_name}}}}}":
            return f"http://{self.service_name}:{self.port}"
        return val

    def _resolve_service_url(self, var_name: str) -> str | None:
        from .constants import _SERVICE_URL_SUFFIX_RE, _FRONTEND_PREFIX_RE, _SERVICE_NAME_MAP

        if not _SERVICE_URL_SUFFIX_RE.search(var_name):
            return None
        stem = _FRONTEND_PREFIX_RE.sub("", var_name)
        stem = _SERVICE_URL_SUFFIX_RE.sub("", stem)
        if stem in _SERVICE_NAME_MAP:
            return f"{{{{SERVICE:{_SERVICE_NAME_MAP[stem]}}}}}"
        for key, svc in _SERVICE_NAME_MAP.items():
            if key.replace("_", "").lower() in stem.replace("_", "").lower():
                return f"{{{{SERVICE:{svc}}}}}"
        return None
