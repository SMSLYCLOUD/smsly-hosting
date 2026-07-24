"""
Zero-Config AI Ecosystem Deployment Engine.

Scans all of a user's GitHub repos, uses AI to analyze each repo's stack,
builds a cross-repo dependency graph, and produces a deploy plan that
can be executed with zero manual configuration.
"""

from .ecosystem_github import (
    _check_github_rate_limit,
    _clone_repo,
    _github_headers,
    _sanitize_git_output,
    fetch_all_repos,
    fetch_file_content,
    fetch_repo_tree,
)

from .ecosystem_heuristics import (
    _detect_addons_from_imports,
    _detect_env_vars,
    _env_plan_map,
    _is_well_known_env_var,
    _merge_deep_env,
    _safe_set,
    BUILD_STRATEGY,
    DB_SIGNALS,
    heuristic_analysis,
    STACK_SIGNALS,
)

from .ecosystem_ai_prompts import (
    _log_ecosystem_prompt,
    ECOSYSTEM_PROMPT,
    get_ecosystem_prompts,
)

from .ecosystem_validation import (
    _deep_sanitize_data,
    _sanitize_ai_response_for_processing,
    _validate_ai_response_structure,
    _validate_field_value,
)

from .ecosystem_intelligence import (
    _ADDON_ALIASES,
    _ai_env_crosscheck,
    _append_tokens,
    _apply_generic_ecosystem_intelligence,
    _apply_plan_repo_defaults,
    _build_deploy_sequence,
    _coerce_addons,
    _coerce_depends_on,
    _dedupe_preserving_order,
    _ensure_100_percent_env_coverage,
    _is_auth_service,
    _is_core_service,
    _is_intelligence_service,
    _normalize_addon_token,
    _normalize_service_plan_fields,
    _rebuild_addons_manifest,
    _repo_short_name,
    _safe_order,
    _unify_cross_service_secrets,
    _unify_same_name_secrets,
)

from .ecosystem_ai_analysis import (
    _attempt_ai_revalidation,
    analyze_ecosystem,
    analyze_ecosystem_chunked,
)

from .ecosystem_pipeline import (
    _build_heuristic_plan,
    _force_merge_scanner_env_vars,
    _scan_and_analyze_impl,
    scan_and_analyze,
)

from .ecosystem_sync import sync_ecosystem_envs

__all__ = [
    "_ADDON_ALIASES",
    "_ai_env_crosscheck",
    "_append_tokens",
    "_apply_generic_ecosystem_intelligence",
    "_apply_plan_repo_defaults",
    "_attempt_ai_revalidation",
    "_build_deploy_sequence",
    "_build_heuristic_plan",
    "_check_github_rate_limit",
    "_clone_repo",
    "_coerce_addons",
    "_coerce_depends_on",
    "_deep_sanitize_data",
    "_dedupe_preserving_order",
    "_detect_addons_from_imports",
    "_detect_env_vars",
    "_ensure_100_percent_env_coverage",
    "_env_plan_map",
    "_force_merge_scanner_env_vars",
    "_github_headers",
    "_is_auth_service",
    "_is_core_service",
    "_is_intelligence_service",
    "_is_well_known_env_var",
    "_log_ecosystem_prompt",
    "_merge_deep_env",
    "_normalize_addon_token",
    "_normalize_service_plan_fields",
    "_rebuild_addons_manifest",
    "_repo_short_name",
    "_safe_order",
    "_safe_set",
    "_sanitize_ai_response_for_processing",
    "_sanitize_git_output",
    "_scan_and_analyze_impl",
    "_unify_cross_service_secrets",
    "_unify_same_name_secrets",
    "_validate_ai_response_structure",
    "_validate_field_value",
    "analyze_ecosystem",
    "analyze_ecosystem_chunked",
    "BUILD_STRATEGY",
    "DB_SIGNALS",
    "ECOSYSTEM_PROMPT",
    "fetch_all_repos",
    "fetch_file_content",
    "fetch_repo_tree",
    "get_ecosystem_prompts",
    "heuristic_analysis",
    "scan_and_analyze",
    "STACK_SIGNALS",
    "sync_ecosystem_envs",
]
