import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError

from ..ecosystem_heuristics import _safe_set, _merge_deep_env, _env_plan_map
from ..ecosystem_ai_prompts import ECOSYSTEM_PROMPT
from ..ecosystem_intelligence import (
    _normalize_service_plan_fields, _apply_plan_repo_defaults, _apply_generic_ecosystem_intelligence,
    _ai_env_crosscheck, _rebuild_addons_manifest, _build_deploy_sequence, _coerce_addons, _coerce_depends_on,
)
from ..ecosystem_pipeline import _force_merge_scanner_env_vars
from .core import analyze_ecosystem

logger = logging.getLogger(__name__)


def analyze_ecosystem_chunked(repos_data: list[dict], github_token: str | None = None, ai_provider: str | None = None, chunk_size: int = 4, existing_services: list | None = None) -> dict:
    global_services: list = []
    global_addons_map: dict = {}

    chunks = [repos_data[i:i + chunk_size] for i in range(0, len(repos_data), chunk_size)]

    def _analyze_single_chunk(idx: int, chunk: list, token: str | None = None, provider: str | None = None):
        try:
            plan = analyze_ecosystem(chunk, token, provider, existing_services=existing_services)
            return idx, plan
        except Exception as exc:
            logger.warning("Ecosystem chunk %d failed: %s", idx, exc)
            return idx, {"error": str(exc), "repos": [r.get("name", "unknown") for r in chunk]}

    results: list[dict | None] = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_analyze_single_chunk, i, chunk, github_token, ai_provider): i
            for i, chunk in enumerate(chunks)
        }
        try:
            for future in as_completed(futures, timeout=600):
                try:
                    idx, plan = future.result()
                    results[idx] = plan
                except Exception as exc:
                    idx = futures[future]
                    results[idx] = {"error": str(exc)}
        except FuturesTimeoutError:
            logger.error("AI Ecosystem chunk analysis timed out.")

    for i, plan in enumerate(results):
        if plan is None:
            plan = {"error": "Chunk processing timed out"}
            results[i] = plan

        logger.info(f"Processing ecosystem chunk {i+1}/{len(chunks)}")
        try:
            from celery import current_task
            if current_task:
                current_task.update_state(
                    state='PROGRESS',
                    meta={'state': f'Processing batch {i+1} of {len(chunks)}...'}
                )
        except Exception as exc:
            logger.debug("Failed to update Celery task state: %s", exc)

        services = plan.get("services", [])
        if not isinstance(services, list):
            services = []
        for svc in services:
            if isinstance(svc, dict):
                global_services.append(svc)

        for addon in plan.get("addons", []):
            if isinstance(addon, dict):
                addon_types = _coerce_addons(addon)
                if addon_types:
                    atype = addon_types[0]
                    try:
                        shared = _coerce_depends_on(addon.get("shared_by", []))
                    except TypeError as exc:
                        logger.warning("Unhashable shared_by for addon %r: %s", addon, exc)
                        shared = []
                    global_addons_map.setdefault(atype, set()).update(shared)

    global_addons = [{"type": k, "shared_by": list(v)} for k, v in global_addons_map.items()]

    if len(chunks) > 1:
        repo_summaries = []
        for rd in repos_data:
            summary = f"\n### REPO: {rd.get('repo', 'unknown')} (Name: {rd.get('repo_name_short', 'unknown')})\n"
            summary += f"Stack: {rd.get('stack', 'unknown')}\n"
            if rd.get('env_vars_context'):
                summary += "Expected Env Vars (with Logic Hints) — INCLUDE ALL OF THESE:\n"
                for var, ctxs in rd['env_vars_context'].items():
                    ctx = ctxs[0] if ctxs else "No context"
                    if len(ctx) > 200:
                        ctx = ctx[:200] + "..."
                    summary += f"- {var}: {ctx}\n"
                env_prefixes = rd.get('env_prefixes', [])
                if env_prefixes:
                    summary += f"  NOTE: This repo uses pydantic env_prefix: {', '.join(env_prefixes)} — all fields are prefixed.\n"
            repo_summaries.append(summary)

        repo_names = [rd.get('repo_name_short') for rd in repos_data if rd.get('repo_name_short')]
        cross_links = []
        for rd in repos_data:
            current_vars = _safe_set(rd.get('env_vars_context', {}).keys())
            for other_rd in repos_data:
                if other_rd['repo'] == rd['repo']:
                    continue
                other_vars = _safe_set(other_rd.get('env_vars_context', {}).keys())
                common = current_vars.intersection(other_vars)
                if common:
                    cross_links.append(f"SHARED STATE: {rd['repo']} and {other_rd['repo']} share env keys: {list(common)}")
            for other in repo_names:
                if other == rd.get('repo_name_short'):
                    continue
                for path, content in rd.get('configs_summary', {}).items():
                    if other in content.lower():
                        cross_links.append(f"DEPENDENCY HINT: {rd['repo']} mentions {other} in {path}")

        try:
            cross_links_deduped = _safe_set(cross_links)
            brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(cross_links_deduped) if cross_links_deduped else ""
        except TypeError:
            cross_links_deduped = _safe_set([str(x) for x in cross_links])
            brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(cross_links_deduped) if cross_links_deduped else ""

        synthesis_prompt = f"""
        You are the Senate Architect performing a FINAL SYNTHESIS pass.
        We have processed a massive ecosystem in batches. Here is the combined JSON plan of all services and addons.

        ### ECOSYSTEM ARCHITECTURAL BRIEF (GLOBAL)
        {brief_header}

        ### REPOSITORY DETAILS (GLOBAL)
        {"".join(repo_summaries)}

        YOUR JOB:
        1. Resolve any cross-repo dependencies. If Service A needs the URL of Service B, ensure Service A's env vars use {{{{SERVICE:service-b}}}}.
        2. Consolidate addons (e.g. ensure only one POSTGRES if they should share).
        3. SHARED SECRET MATCHING (CRITICAL): Identify inter-service auth secrets that have DIFFERENT names on different services but MUST hold the SAME value. For example:
           - `POLICY_TO_AUDIT_SECRET` on policy-service = `AUDIT_SERVICE_SECRET` on audit-service
           - `RATELIMIT_SECRET` on any service = `RATE_LIMIT_*_SECRET` on rate-limit-service (which uses env_prefix="RATE_LIMIT_")
           - `PLATFORM_API_SECRET` = `RATE_LIMIT_PLATFORM_API_SECRET`
           When you find such pairs, assign them to the SAME {{{{SHARED_SECRET:common_name}}}} placeholder.
        4. EXHAUSTIVE ENV VARS (MANDATORY): You MUST include EVERY SINGLE variable listed under "Expected Env Vars" in the REPOSITORY DETAILS for each service. Do NOT omit any variables. Map them to the appropriate {{{{SERVICE:...}}}}, {{{{POSTGRES_URL}}}}, or {{{{SHARED_SECRET:...}}}} placeholder. For secrets that need random generation (API keys, tokens, passwords), set {{"generate": true}} in the env entry. For unknown non-secret vars, leave the value empty.
        5. FULL DEPLOY ORDER AUTHORITY: You have complete power to restructure the "deploy_order" and "deploy_sequence" from scratch to ensure a successful deployment (e.g., Auth/Identity -> Core API -> Gateways -> Frontends).

        CURRENT COMBINED PLAN:
        ```json
        {json.dumps({"services": global_services, "addons": global_addons}, indent=2)}
        ```

        CRITICAL TYPE RULES — violation will crash the system:
        - ALL array fields ("depends_on", "shared_by", service-level "addons", "deploy_sequence") must contain ONLY strings, NEVER objects.
        - "env_vars" values must be strings ONLY, never objects or arrays.
        - Every service in "services" must be a flat object; no arrays within arrays.

        Return ONLY valid JSON matching this exact structure:
        {{
          "ecosystem_name": "Synthesized Ecosystem",
          "services": [...],
          "addons": [...]
        }}
        """

        logger.info("=== SYNTHESIS PROMPT SENT TO AI ===")
        logger.info(f"Chunks processed: {len(chunks)}")
        logger.info(f"Global services count: {len(global_services)}")
        logger.info(f"Global addons count: {len(global_addons)}")
        logger.info("Synthesis prompt preview:")
        synthesis_preview = synthesis_prompt[:1000] if len(synthesis_prompt) > 1000 else synthesis_prompt
        logger.info(synthesis_preview)
        if len(synthesis_prompt) > 1000:
            logger.info("... [synthesis prompt truncated] ...")

        try:
            from apps.intelligence.providers import _cached_ask
            response_text, provider = _cached_ask(synthesis_prompt, system_prompt=ECOSYSTEM_PROMPT, provider_id=ai_provider)
            response_text = response_text or ""

            logger.info("=== SYNTHESIS AI RESPONSE RECEIVED ===")
            logger.info(f"Response provider: {provider}")
            logger.info(f"Response length: {len(response_text)} characters")
            logger.info("Synthesis response preview:")
            synth_preview = response_text[:1000] if len(response_text) > 1000 else response_text
            logger.info(synth_preview)
            if len(response_text) > 1000:
                logger.info("... [synthesis response truncated] ...")

            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            if start_idx != -1 and end_idx != -1 and start_idx <= end_idx:
                json_str = response_text[start_idx:end_idx+1]
                synth_plan = json.loads(json_str)
                raw_svcs = synth_plan.get("services")
                if isinstance(raw_svcs, list):
                    global_services = [s for s in raw_svcs if isinstance(s, dict)]
                raw_addons = synth_plan.get("addons")
                if isinstance(raw_addons, list):
                    global_addons = [a for a in raw_addons if isinstance(a, dict)]
        except Exception as e:
            logger.warning("=== SYNTHESIS PASS FAILED ===")
            logger.warning(f"Error: {e}")
            logger.info("Synthesis pass failed, using raw merged plan")

    sanitized_services = []
    for svc in global_services:
        if not isinstance(svc, dict):
            continue
        try:
            _normalize_service_plan_fields(svc)
            sanitized_services.append(svc)
        except Exception as exc:
            logger.warning("Skipping unprocessable service %r: %s", svc.get("repo", "?"), exc)
    global_services = sanitized_services

    recon_added = 0
    covered_repos = set()
    for svc in global_services:
        r = str(svc.get("repo", "")).strip().lower()
        if r:
            covered_repos.add(r)
    for rd in repos_data:
        repo_full = str(rd.get("repo", "")).strip().lower()
        if not repo_full or repo_full in covered_repos:
            continue
        h = rd.get("heuristic", {})
        name = repo_full.split("/")[-1]
        env_map = _env_plan_map(h.get("env_vars", []))
        deep_env = rd.get("env_vars_context", {})
        if deep_env:
            env_map = _merge_deep_env(env_map, deep_env)
        recon_svc = {
            "repo": repo_full,
            "name": name,
            "branch": str(rd.get("default_branch") or "main"),
            "stack": h.get("stack", "unknown"),
            "port": h.get("port", 3000),
            "build": h.get("build", "nixpacks"),
            "addons": h.get("addons", []),
            "env_vars": env_map,
            "depends_on": [],
            "deploy_order": 99,
        }
        global_services.append(recon_svc)
        covered_repos.add(repo_full)
        recon_added += 1
        logger.warning(
            "Reconciliation: repo %s had no service in any chunk plan — "
            "auto-created from heuristic data.", repo_full
        )
    if recon_added:
        logger.info(
            "Ecosystem reconciliation added %d service(s) that were "
            "missing from AI output.", recon_added
        )

    try:
        for svc in global_services:
            repo = svc.get("repo", "")
            for rd in repos_data:
                if rd.get("repo") == repo:
                    svc["_env_prefixes"] = list(rd.get("env_prefixes", []))
                    svc["_is_heavy"] = rd.get("is_heavy", False)
                    break

        _apply_plan_repo_defaults(global_services, repos_data)
        _force_merge_scanner_env_vars(global_services, repos_data)
        _apply_generic_ecosystem_intelligence(global_services)
        _ai_env_crosscheck(global_services, ai_provider)
    except TypeError as exc:
        logger.warning("TypeError during ecosystem intelligence processing: %s", exc)
    except Exception as exc:
        logger.warning("Unexpected error during ecosystem intelligence processing: %s", exc)

    try:
        final_addons = _rebuild_addons_manifest(global_services, global_addons)
    except Exception as exc:
        logger.warning("Addon manifest rebuild failed: %s", exc)
        final_addons = []

    try:
        deploy_sequence = _build_deploy_sequence(global_services)
    except Exception as exc:
        logger.warning("Deploy sequence build failed: %s", exc)
        deploy_sequence = ["addons"]

    return {
        "ecosystem_name": "SMSLY Auto-Generated Ecosystem",
        "services": global_services,
        "addons": final_addons,
        "deploy_sequence": deploy_sequence,
        "ai_provider": ai_provider or "auto"
    }
