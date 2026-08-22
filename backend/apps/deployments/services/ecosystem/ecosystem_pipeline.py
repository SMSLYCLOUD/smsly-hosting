"""
Zero-Config AI Ecosystem Deployment Engine — Pipeline stage.

Holds the top-level orchestration functions: scanning, heuristic fallback,
AI analysis dispatch, and env-var merging.
"""

import json
import logging
from datetime import UTC

from .ecosystem_ai_prompts import ECOSYSTEM_PROMPT
from .ecosystem_github import fetch_all_repos, fetch_repo_tree
from .ecosystem_heuristics import (
    _env_plan_map,
    _merge_deep_env,
    _secretish_fill_value,
    heuristic_analysis,
)
from .ecosystem_intelligence import (
    _apply_generic_ecosystem_intelligence,
    _build_deploy_sequence,
    _rebuild_addons_manifest,
    _safe_order,
)
from .ecosystem_validation import _sanitize_ai_response_for_processing, _validate_ai_response_structure

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Post-AI env-var merge safety net
# ──────────────────────────────────────────────────────────────────────────────

def _force_merge_scanner_env_vars(services: list[dict], repos_data: list[dict]):
    """Post-AI safety net: merge EVERY scanner-detected env var into the plan.

    The AI is strictly forbidden from dropping any env var found by the
    RepoScanner.  This function runs BEFORE _apply_generic_ecosystem_intelligence
    so that cross-service secret mapping (e.g. GATEWAY_SECRET ->
    {{SHARED_SECRET:gateway_secret}}) fires for vars the AI omitted.

    Vars are filled with the concrete value found in the repo's own files
    (.env.example, Dockerfile ENV, compose environment) whenever one exists —
    an empty value must never override the codebase's default.  Missing vars
    get ``{{GENERATE}}`` only for internal secrets; third-party provider keys
    (OPENAI_API_KEY, STRIPE_SECRET_KEY, ...) stay empty for the user to fill.
    """
    repo_to_deep: dict[str, dict[str, list[str]]] = {}
    repo_to_values: dict[str, dict[str, str]] = {}
    for rd in repos_data:
        repo = str(rd.get("repo") or "").strip().lower()
        deep = rd.get("env_vars_context")
        if repo and isinstance(deep, dict):
            repo_to_deep[repo] = deep
        defaults = rd.get("env_var_defaults")
        if repo and isinstance(defaults, dict):
            repo_to_values[repo] = {
                str(k).strip().upper(): str(v) for k, v in defaults.items()
            }

    for svc in services:
        if not isinstance(svc, dict):
            continue
        repo = str(svc.get("repo") or "").strip().lower()
        deep_env = repo_to_deep.get(repo)
        if not deep_env:
            continue
        file_values = repo_to_values.get(repo, {})

        env_map = svc.get("env_vars")
        if not isinstance(env_map, dict):
            env_map = {}
            svc["env_vars"] = env_map

        added = 0
        for var_name, contexts in deep_env.items():
            upper_key = var_name.upper().strip()
            if not upper_key:
                continue
            if upper_key in env_map:
                continue
            file_value = file_values.get(upper_key, "").strip()
            if file_value:
                env_map[upper_key] = file_value
            else:
                env_map[upper_key] = _secretish_fill_value(upper_key)
            added += 1

        if added:
            logger.info(
                "Post-AI env-var merge: added %d scanner-detected var(s) "
                "that AI dropped for %s", added, repo,
            )


# ──────────────────────────────────────────────────────────────────────────────
# AI-Powered Ecosystem Analysis
# ──────────────────────────────────────────────────────────────────────────────


def _build_heuristic_plan(repos_data: list[dict], error: str = "") -> dict:
    """Build a basic deploy plan from heuristics when AI fails."""
    services = []
    order = 1
    skipped = 0

    for rd in repos_data:
        h = rd.get("heuristic", {})
        stack = h.get("stack", "unknown")
        if stack == "unknown":
            logger.warning(
                "Repo %s has unknown stack (no package.json, manage.py, etc. found). "
                "Including with 'unknown' stack — deploy may need manual stack selection.",
                rd.get("repo", "?"),
            )
            skipped += 1

        name = rd["repo"].split("/")[-1]

        # Prefer deep-scan env_vars_context if available (from RepoScanner in analyze_ecosystem).
        # Fall back to heuristic filename-based detection otherwise.
        env_vars_raw = h.get("env_vars", [])
        env_map = _env_plan_map(env_vars_raw)
        deep_env = rd.get("env_vars_context", {})
        if deep_env:
            env_map = _merge_deep_env(env_map, deep_env, rd.get("env_var_defaults"))

        svc = {
            "repo": rd["repo"],
            "name": name,
            "branch": str(rd.get("default_branch") or "main"),
            "stack": stack,
            "port": h.get("port", 3000),
            "build": h.get("build", "nixpacks"),
            "addons": h.get("addons", []),
            "env_vars": env_map,
            "depends_on": [],
            "deploy_order": order,
            "_is_heavy": rd.get("is_heavy", False),
        }

        services.append(svc)
        order += 1

    if skipped:
        logger.info(
            "%d repos had unknown stacks and were included as-is "
            "(user should set stack manually in the dashboard).", skipped
        )

    # Sort: backends before frontends
    backend_stacks = {"django", "python", "rust", "go", "java", "ruby", "elixir", "php"}
    backends = [s for s in services if s["stack"] in backend_stacks]
    frontends = [s for s in services if s["stack"] not in backend_stacks]

    sorted_services = []
    for i, s in enumerate(backends + frontends, 1):
        s["deploy_order"] = i
        sorted_services.append(s)

    _apply_generic_ecosystem_intelligence(sorted_services)
    addons_list = _rebuild_addons_manifest(sorted_services, [])
    deploy_sequence = _build_deploy_sequence(sorted_services)

    return {
        "services": sorted_services,
        "addons": addons_list,
        "deploy_sequence": deploy_sequence,
        "ai_provider": f"Heuristic (AI parse failed: {error})" if error else "Heuristic",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Full Scan Pipeline
# ──────────────────────────────────────────────────────────────────────────────

def scan_and_analyze(token: str, ai_provider: str | None = None, selected_repos: list | None = None, existing_services: list | None = None, scan_window_days: int | None = None, scan_depth: str = 'shallow') -> dict:
    """
    Full pipeline: fetch all repos → analyze each → AI ecosystem plan.
    If selected_repos is provided, only processes those specific repositories.
    If scan_window_days is provided, filters repos by recency (pushed_at).
    If scan_depth is provided, controls how deeply each repo is scanned for env vars.

    Returns the deploy plan dict ready for the frontend.
    """
    logger.info("Starting ecosystem scan...")
    try:
        return _scan_and_analyze_impl(token, ai_provider=ai_provider, selected_repos=selected_repos, existing_services=existing_services, scan_window_days=scan_window_days, scan_depth=scan_depth)
    except TypeError as exc:
        logger.exception("Ecosystem scan failed with unhashable type error: %s", exc)
        return {
            "error": f"Scan failed: {exc!s}. This is usually caused by unexpected AI response data.",
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
        }
    except Exception as exc:
        logger.exception("Ecosystem scan failed unexpectedly: %s", exc)
        return {
            "error": f"Scan failed: {exc!s}",
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
        }


def _scan_and_analyze_impl(token: str, ai_provider: str | None = None, selected_repos: list | None = None, existing_services: list | None = None, scan_window_days: int | None = None, scan_depth: str = 'shallow') -> dict:
    """Internal implementation of scan_and_analyze."""
    from datetime import datetime, timedelta

    from .ecosystem_ai_analysis import analyze_ecosystem_chunked

    logger.info("=== STARTING ECOSYSTEM SCAN ===")

    # 1. Fetch all repos
    logger.info("Step 1: Fetching repositories...")
    all_repos = fetch_all_repos(token)
    logger.info(f"Found {len(all_repos)} repositories")

    # Filter by recency if scan_window_days is set
    if scan_window_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=scan_window_days)
        before = len(all_repos)
        all_repos = [
            r for r in all_repos
            if r.get("pushed_at") and datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) >= cutoff
        ]
        logger.info(f"Filtered by scan_window_days={scan_window_days}: {before} → {len(all_repos)} repos")

    # Filter by user selection if provided
    logger.info(f"Filtering by selected repos: {selected_repos}")
    if isinstance(selected_repos, list):
        all_repos = [r for r in all_repos if r.get("full_name") in selected_repos]
    elif isinstance(selected_repos, str):
        all_repos = [r for r in all_repos if r.get("full_name") == selected_repos]
    else:
        logger.warning("selected_repos is unexpected type %s, skipping filter", type(selected_repos).__name__)
    logger.info(f"Filtered down to {len(all_repos)} selected repositories")

    # 2. Analyze each repo
    logger.info("Step 2: Analyzing repositories...")
    repos_data = []
    scan_warnings = []
    skipped_forks = 0
    skipped_empty = 0
    skipped_errors = 0
    for repo in all_repos:
        full_name = repo["full_name"]
        description = repo.get("description", "") or ""
        default_branch = repo.get("default_branch", "main")
        is_fork = repo.get("fork", False)

        # Skip forks and empty repos
        if is_fork:
            skipped_forks += 1
            continue
        if repo.get("size", 0) == 0:
            skipped_empty += 1
            continue

        # Fetch file tree. A single inaccessible repo should not fail the
        # whole ecosystem scan.
        try:
            files = fetch_repo_tree(token, full_name, default_branch)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Skipping %s during ecosystem scan: %s", full_name, exc)
            scan_warnings.append(f"{full_name}: {exc}")
            skipped_errors += 1
            continue
        if not files:
            skipped_errors += 1
            continue

        # Quick heuristic analysis
        heuristic = heuristic_analysis(files)

        # Use the scan_depth parameter for all repos in this ecosystem scan
        repos_data.append({
            "repo": full_name,
            "description": description,
            "default_branch": default_branch,
            "files": files,
            "heuristic": heuristic,
            "private": repo.get("private", False),
            "scan_depth": scan_depth,
        })

    if skipped_forks or skipped_empty or skipped_errors:
        logger.info(
            "Repo filter summary: %d skipped (forks=%d, empty=%d, errors=%d), %d kept",
            skipped_forks + skipped_empty + skipped_errors,
            skipped_forks, skipped_empty, skipped_errors,
            len(repos_data),
        )

    if not repos_data:
        logger.info("No deployable repositories found")
        message_parts = []
        if skipped_forks:
            message_parts.append(f"{skipped_forks} fork(s) excluded")
        if skipped_empty:
            message_parts.append(f"{skipped_empty} empty repos(s) excluded")
        if skipped_errors:
            message_parts.append(f"{skipped_errors} repo(s) inaccessible")
        msg = "No deployable repositories found."
        if message_parts:
            msg += f" ({'; '.join(message_parts)})"
        return {
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
            "message": msg,
        }

    logger.info(f"Step 3: Analyzing {len(repos_data)} repos with AI...")

    # 3. AI ecosystem analysis (CHUNKED)
    logger.info("Starting AI ecosystem analysis...")
    try:
        plan = analyze_ecosystem_chunked(repos_data, github_token=token, ai_provider=ai_provider, existing_services=existing_services)
        logger.info("AI analysis completed successfully")
    except Exception as e:
        if ai_provider:
            logger.error(f"AI ecosystem analysis failed with provider '{ai_provider}': {e}")
            raise
        logger.error(f"AI ecosystem analysis failed (no provider): {e}")
        return _build_heuristic_plan(repos_data, f"AI analysis failed: {e!s}")

    # 4. AI REVALIDATION: Validate and sanitize AI response before final submission
    logger.info("Step 4: Performing AI response revalidation...")
    try:
        if not _validate_ai_response_structure(json.dumps(plan)):
            logger.warning("AI response validation failed, attempting revalidation...")
            logger.warning(f"Problematic plan structure: {json.dumps(plan, indent=2)[:500]}...")

            # If validation fails, try to get a corrected response from AI
            revalidation_prompt = f"""
            CRITICAL: Your previous ecosystem plan was rejected due to invalid data structure.
            The plan must contain ONLY:
            - "services": Array of objects with string fields only (no nested objects in env_vars, addons, depends_on)
            - "addons": Array of objects with string fields only
            - "deploy_sequence": Array of strings
            - "ai_provider": String

            PREVIOUS PLAN (invalid):
            {json.dumps(plan, indent=2)}

            Return ONLY a valid JSON ecosystem plan with the correct structure:
            {{
              "ecosystem_name": "SMSLY Auto-Generated Ecosystem",
              "services": [
                {{
                  "name": "service-name",
                  "repo": "owner/repo",
                  "stack": "python",
                  "env_vars": {{"KEY": "value"}},
                  "addons": ["POSTGRES", "REDIS"],
                  "depends_on": ["other-service"],
                  "deploy_order": 50
                }}
              ],
              "addons": [
                {{
                  "type": "POSTGRES",
                  "shared_by": ["service-1", "service-2"]
                }}
              ],
              "deploy_sequence": ["addons", "service-1", "service-2"],
              "ai_provider": "auto"
            }}
            """

            try:
                from apps.intelligence.providers import _cached_ask
                response_text, _provider = _cached_ask(
                    revalidation_prompt,
                    system_prompt=ECOSYSTEM_PROMPT,
                    provider_id=ai_provider
                )

                logger.info("Revalidation response received, validating...")
                if _validate_ai_response_structure(response_text):
                    plan = _sanitize_ai_response_for_processing(response_text)
                    logger.info("AI response revalidation successful")
                else:
                    logger.error("AI revalidation also failed, falling back to heuristic plan")
                    plan = _build_heuristic_plan(repos_data, "AI response structure validation failed after revalidation")
            except Exception as e:
                logger.error(f"AI revalidation failed: {e}")
                plan = _build_heuristic_plan(repos_data, f"AI revalidation failed: {e!s}")

        else:
            logger.info("AI response validation passed on first attempt")

    except Exception as e:
        logger.error(f"AI revalidation process failed: {e}")
        plan = _build_heuristic_plan(repos_data, f"AI revalidation process failed: {e!s}")

    # 5. FINAL VALIDATION: Ensure the returned plan is safe for processing
    logger.info("Step 5: Performing final validation...")
    try:
        final_plan = {
            "ecosystem_name": plan.get("ecosystem_name", "SMSLY Auto-Generated Ecosystem"),
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": plan.get("ai_provider", "auto"),
            "total_repos_scanned": len(all_repos),
            "deployable_repos": len(repos_data),
            "scan_warning_count": len(scan_warnings),
        }

        if scan_warnings:
            final_plan["scan_warnings"] = scan_warnings[:20]

        # Safely extract and validate services
        logger.info(f"Processing {len(plan.get('services', []))} services...")

        # Track which repos are covered by services in the plan.
        covered_repos_in_plan = set()

        for i, service in enumerate(plan.get("services", [])):
            if isinstance(service, dict):
                try:
                    # Check if already deployed
                    str(service.get("name", f"service-{i}")).lower()
                    service_repo = str(service.get("repo", "")).lower()

                    if service_repo:
                        covered_repos_in_plan.add(service_repo)

                    # Ensure all critical fields are strings or can be converted to strings
                    safe_service = {
                        "name": str(service.get("name", f"service-{i}")),
                        "repo": str(service.get("repo", "")),
                        "stack": str(service.get("stack", "unknown")),
                        "env_vars": {str(k): str(v) for k, v in service.get("env_vars", {}).items()},
                        "addons": [str(a) for a in service.get("addons", [])],
                        "depends_on": [str(d) for d in service.get("depends_on", [])],
                        "deploy_order": _safe_order(service.get("deploy_order"), 50),
                        "skip": bool(service.get("skip", False))
                    }
                    # Preserve ALL additional fields from the AI plan
                    _PASSTHROUGH_KEYS = {
                        "branch", "port", "build", "description", "dockerfile",
                        "cmd", "entrypoint", "volumes", "networks", "restart",
                        "deploy", "labels", "healthcheck", "resources",
                        "config", "env_file", "compose", "env_prefix",
                    }
                    for k in _PASSTHROUGH_KEYS:
                        if k in service and k not in safe_service:
                            safe_service[k] = service[k]
                    final_plan["services"].append(safe_service)
                    logger.info(f"Successfully processed service: {safe_service['name']} (skip={safe_service['skip']})")
                except Exception as e:
                    logger.warning(f"Error processing service {i}: {e}")
                    logger.warning(f"Problematic service data: {service}")
                    continue

        # === DIFF CHECK: flag any input repo that has no matching service ===
        orphan_repos = []
        for rd in repos_data:
            repo_full = str(rd.get("repo", "")).strip().lower()
            if repo_full and repo_full not in covered_repos_in_plan:
                orphan_repos.append(rd.get("repo", "?"))
        if orphan_repos:
            warning_msg = (
                f"{len(orphan_repos)} repo(s) had no matching service in the final plan: "
                f"{', '.join(orphan_repos[:10])}"
            )
            if len(orphan_repos) > 10:
                warning_msg += f" ... and {len(orphan_repos) - 10} more"
            logger.warning(warning_msg)
            scan_warnings.append(warning_msg)
            final_plan["scan_warning_count"] = len(scan_warnings)
            if scan_warnings:
                final_plan["scan_warnings"] = scan_warnings[:20]
            final_plan["orphan_repos"] = orphan_repos

        # Safely extract and validate addons
        logger.info(f"Processing {len(plan.get('addons', []))} addons...")
        for i, addon in enumerate(plan.get("addons", [])):
            if isinstance(addon, dict):
                try:
                    safe_addon = {
                        "type": str(addon.get("type", f"addon-{i}")),
                        "shared_by": [str(s) for s in addon.get("shared_by", [])]
                    }
                    final_plan["addons"].append(safe_addon)
                    logger.info(f"Successfully processed addon: {safe_addon['type']}")
                except Exception as e:
                    logger.warning(f"Error processing addon {i}: {e}")
                    logger.warning(f"Problematic addon data: {addon}")
                    continue

        # Build deploy sequence safely
        logger.info("Building deploy sequence...")
        try:
            final_plan["deploy_sequence"] = _build_deploy_sequence(final_plan["services"])
            logger.info(f"Deploy sequence built: {final_plan['deploy_sequence']}")
        except Exception as e:
            logger.warning(f"Error building deploy sequence: {e}")
            # Fallback: just use service names in order
            try:
                fallback_sequence = ["addons"] + [
                    str(svc.get("name", f"service-{i}"))
                    for i, svc in enumerate(final_plan["services"])
                ]
                final_plan["deploy_sequence"] = fallback_sequence
                logger.info(f"Fallback deploy sequence: {fallback_sequence}")
            except Exception:
                final_plan["deploy_sequence"] = ["addons"]
                logger.warning("Using minimal deploy sequence")

        logger.info("=== ECOSYSTEM SCAN COMPLETED SUCCESSFULLY ===")
        return final_plan

    except Exception as e:
        logger.error(f"Final validation failed, returning safe fallback: {e}")
        logger.error(f"Error details: {type(e).__name__}: {e!s}")
        return _build_heuristic_plan(repos_data, f"Final validation failed: {e!s}")
