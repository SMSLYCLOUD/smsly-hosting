from .addons import (
    _ADDON_ALIASES,
    _coerce_addons,
    _normalize_addon_token,
)
from .ai_crosscheck import _ai_env_crosscheck
from .classification import (
    _is_auth_service,
    _is_core_service,
    _is_intelligence_service,
)
from .deploy_sequence import (
    _build_deploy_sequence,
    _rebuild_addons_manifest,
)
from .helpers import (
    _append_tokens,
    _coerce_depends_on,
    _dedupe_preserving_order,
    _normalize_service_plan_fields,
    _repo_short_name,
    _safe_order,
)
from .main import (
    _apply_generic_ecosystem_intelligence,
    _apply_plan_repo_defaults,
    _ensure_100_percent_env_coverage,
)
from .secret_unification import (
    _unify_cross_service_secrets,
    _unify_same_name_secrets,
)

__all__ = [
    "_ADDON_ALIASES",
    "_ai_env_crosscheck",
    "_apply_generic_ecosystem_intelligence",
    "_apply_plan_repo_defaults",
    "_append_tokens",
    "_build_deploy_sequence",
    "_coerce_addons",
    "_coerce_depends_on",
    "_dedupe_preserving_order",
    "_ensure_100_percent_env_coverage",
    "_is_auth_service",
    "_is_core_service",
    "_is_intelligence_service",
    "_normalize_addon_token",
    "_normalize_service_plan_fields",
    "_rebuild_addons_manifest",
    "_repo_short_name",
    "_safe_order",
    "_unify_cross_service_secrets",
    "_unify_same_name_secrets",
]
