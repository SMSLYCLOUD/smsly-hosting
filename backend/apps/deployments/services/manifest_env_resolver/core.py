import os

from .constants import (
    ADDON_ENV_PATTERNS,
    DEPLOY_TIME_VARS,
    SECRET_PATTERNS,
    _FRONTEND_PREFIX_RE,
    _SECRET_EXCLUSIONS,
    _SERVICE_NAME_MAP,
    generate_strong_secret,
)


class CoreMixin:
    def resolve_all(self) -> dict[str, str]:
        if not self.source_dir or not os.path.isdir(self.source_dir):
            return {}
        self._scan_env_example()
        self._scan_secrets_manifest()
        self._detect_stack()
        self._detect_port()
        self._detect_frontend()

        resolved: dict[str, str] = {}

        for var_name, default_val in self.env_example_vars.items():
            value = self._resolve_var(var_name, default_val)
            if value is not None:
                valid_value = self._sanitize_value(var_name, value)
                if valid_value is not None:
                    resolved[var_name] = valid_value

        self._inject_stack_defaults(resolved)

        self.resolved_env = resolved
        return resolved

    def _resolve_var(self, var_name: str, default_val: str) -> str | None:
        if var_name in DEPLOY_TIME_VARS:
            return None

        cross_secret = self._resolve_cross_service_secret(var_name)
        if cross_secret:
            return cross_secret

        if var_name in ADDON_ENV_PATTERNS:
            return ADDON_ENV_PATTERNS[var_name]

        service_url = self._resolve_service_url(var_name)
        if service_url:
            return service_url

        if default_val == "":
            stack_default = self._get_stack_default(var_name)
            if stack_default is not None:
                self.heuristic_vars.append(var_name)
                return stack_default

        if default_val == "":
            heuristic = self._get_heuristic_default(var_name)
            if heuristic is not None:
                self.heuristic_vars.append(var_name)
                return heuristic

        if SECRET_PATTERNS.search(var_name) and not _SECRET_EXCLUSIONS.search(var_name):
            return generate_strong_secret(48)

        if default_val == "" and var_name in ("SERVICE_NAME", "OTEL_SERVICE_NAME"):
            return self.service_name

        if default_val:
            return default_val

        if self.is_frontend and _FRONTEND_PREFIX_RE.match(var_name):
            frontend_stem = _FRONTEND_PREFIX_RE.sub("", var_name)
            svc = _SERVICE_NAME_MAP.get(frontend_stem.replace("-", "_").upper())
            if svc:
                return f"{{{{SERVICE:{svc}}}}}"

        if any(
            p in var_name
            for p in (
                "SERVICE_URL",
                "_URL",
                "DSN",
                "DIR",
                "BACKEND",
                "FALLBACK",
                "PATH",
                "PREFIX",
                "ENDPOINT",
                "CACHE_",
                "_KEY",
                "_SECRET",
                "API_KEY",
                "TOKEN",
                "PASSWORD",
                "EXTERNAL",
                "_TTL_DAYS",
                "_TTL_HOURS",
                "_INTERVAL",
                "_THRESHOLD",
                "_WEIGHT_",
                "_SCORE_",
                "COLLECT_",
                "DECAY_",
                "SIGNAL_",
                "ANOMALY_",
            )
        ):
            self.heuristic_vars.append(var_name)
            return ""

        mock_value = self._generate_mock_for_var(var_name)
        if mock_value:
            self.heuristic_vars.append(var_name)
            return mock_value

        self.unresolved_vars.append(var_name)
        return ""

    def _sanitize_value(self, var_name: str, value: str) -> str | None:
        if var_name == "PORT" or var_name.endswith("_PORT"):
            return None
        return value
