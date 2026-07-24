import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError

from .ecosystem_github import _clone_repo
from .ecosystem_heuristics import _safe_set, _merge_deep_env, _env_plan_map
from .ecosystem_ai_prompts import ECOSYSTEM_PROMPT, _log_ecosystem_prompt
from .ecosystem_validation import _validate_ai_response_structure, _sanitize_ai_response_for_processing
from .ecosystem_intelligence import (
    _normalize_service_plan_fields, _apply_plan_repo_defaults, _apply_generic_ecosystem_intelligence,
    _ai_env_crosscheck, _rebuild_addons_manifest, _build_deploy_sequence, _coerce_addons, _coerce_depends_on,
)
from .ecosystem_pipeline import _force_merge_scanner_env_vars, _build_heuristic_plan

logger = logging.getLogger(__name__)


def analyze_ecosystem(repos_data: list[dict], github_token: str | None = None, ai_provider: str | None = None, existing_services: list | None = None) -> dict:
    """
    Use AI Senate to analyze all repos together in a temporary workspace.
    Clones repos, scans for cross-repo dependencies, and produces a plan.
    """

    # 1. Create a temporary workspace for the analysis
    with tempfile.TemporaryDirectory(prefix="cloud-ecosystem-") as workspace_dir:
        logger.info(f"Created ecosystem workspace: {workspace_dir}")

        # 2. Clone all repos into the workspace
        for rd in repos_data:
            local_path = rd.get('local_path')
            if local_path and os.path.exists(local_path):
                logger.info(f"Bypassing clone, using local path directly: {local_path}")
                rd['clone_dir'] = local_path
                rd['repo_name_short'] = os.path.basename(local_path)
                continue

            repo_full = rd.get('repo')
            if not repo_full:
                continue

            repo_name = repo_full.split('/')[-1]
            target_dir = os.path.join(workspace_dir, repo_name)

            success = _clone_repo(repo_full, target_dir, github_token)
            if success:
                rd['clone_dir'] = target_dir
                rd['repo_name_short'] = repo_name
            else:
                logger.warning(f"Failed to clone {repo_full} for analysis")

        # 3. Aggressive Multi-Repo Scanning
        for rd in repos_data:
            clone_dir = rd.get('clone_dir')
            if clone_dir:
                from apps.intelligence.scanner import RepoScanner
                scanner = RepoScanner(clone_dir)
                scan = scanner.scan()
                rd['env_vars_context'] = scan.get('env_vars_context', {})
                rd['env_prefixes'] = scan.get('env_prefixes', [])
                rd['stack'] = scan.get('stack', rd.get('stack', 'unknown'))

                # Intelligent Config Extraction (Context Size Optimization)
                configs_summary = {}
                priority_files = ['docker-compose.yml', 'docker-compose.yaml', 'Dockerfile', 'package.json', 'requirements.txt', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'config.py', 'settings.py', 'main.py', 'app.py']

                raw_configs = scan.get('configs', {})
                critical_configs = [(k, v) for k, v in raw_configs.items() if any(p in os.path.basename(k) for p in priority_files)]

                # Sort by priority, then limit to top 4 files to prevent token bloat.
                # Bind `priority_files` explicitly to insulate the closure from any
                # later re-binding of that name in the enclosing loop.
                def _sort_key(item, _priority_files=priority_files):
                    bname = os.path.basename(item[0])
                    for i, pf in enumerate(_priority_files):
                        if pf in bname:
                            return i
                    return 99

                critical_configs.sort(key=_sort_key)

                for k, v in critical_configs[:4]:
                    bname = os.path.basename(k)
                    if 'package.json' in bname:
                        try:
                            parsed = json.loads(v)
                            slim = {
                                "scripts": parsed.get("scripts", {}),
                                "dependencies": parsed.get("dependencies", {}),
                            }
                            configs_summary[k] = json.dumps(slim, indent=2)
                        except Exception:
                            configs_summary[k] = v[:300] + "\n...[truncated]"
                    elif 'Dockerfile' in bname or 'docker-compose' in bname:
                        # Keep full logic but strip comments and blanks
                        lines = [line for line in v.split('\n') if line.strip() and not line.strip().startswith('#')]
                        configs_summary[k] = '\n'.join(lines)[:800]
                    else:
                        configs_summary[k] = v[:300] + "\n...[truncated]"

                rd['configs_summary'] = configs_summary
                rd['structure'] = scan.get('structure', '')

        # 4. Build the Cross-Repo Intelligence Brief
        repo_summaries = []
        for rd in repos_data:
            summary = f"\n### REPO: {rd['repo']} (Name: {rd.get('repo_name_short', 'unknown')})\n"
            summary += f"Description: {rd.get('description', 'No description')}\n"
            summary += f"Stack: {rd.get('stack', 'unknown')}\n"

            # Detect resource intensity
            is_heavy = False
            for _file_path, content in rd.get('configs_summary', {}).items():
                if any(lib in content.lower() for lib in ['torch', 'tensorflow', 'nvidia', 'java', 'spring', 'elasticsearch']):
                    is_heavy = True
                    break
            rd['is_heavy'] = is_heavy
            summary += f"Resource Intensity: {'HEAVY (Requires 2GB+ RAM)' if is_heavy else 'STANDARD'}\n"

            if rd.get('env_vars_context'):
                summary += "Expected Env Vars (with Logic Hints) — INCLUDE ALL OF THESE:\n"
                for var, ctxs in sorted(rd['env_vars_context'].items()):
                    ctx = ctxs[0] if ctxs else "No context"
                    if len(ctx) > 200:
                        ctx = ctx[:200] + "..."
                    summary += f"- {var}: {ctx}\n"
                # Show env_prefix info if detected
                env_prefixes = rd.get('env_prefixes', [])
                if env_prefixes:
                    summary += f"  NOTE: This repo uses pydantic env_prefix: {', '.join(env_prefixes)} "
                    summary += "— all fields are prefixed (e.g. PREFIX_VAR corresponds to VAR on other services).\n"

            if rd.get('configs_summary'):
                summary += "Critical Config Analysis (use to find ALL field declarations):\n"
                for path, snippet in rd['configs_summary'].items():
                    if any(p in path for p in ['Dockerfile', 'compose', 'package', 'requirements', 'settings', 'config', 'main', 'app', 'urls']):
                        summary += f"#### FILE: {path}\n```\n{snippet}\n```\n"

            repo_summaries.append(summary)

        # 5. Global Linkage & Discovery Analysis
        cross_links = []
        if existing_services:
            existing_desc = "ALREADY DEPLOYED SERVICES IN ECOSYSTEM (use for cross-linking):\n"
            for s in existing_services:
                existing_desc += f"- Service Name: {s.get('name')} | Repository URL: {s.get('repository_url') or 'unknown'} | Internal Port: {s.get('internal_port') or 3000}\n"
            cross_links.append(existing_desc)

        repo_names = [rd.get('repo_name_short') for rd in repos_data if rd.get('repo_name_short')]
        for rd in repos_data:
            cd = rd.get('clone_dir')
            if not cd:
                continue

            # Look for environment variable overlaps
            current_vars = _safe_set(rd.get('env_vars_context', {}).keys())
            for other_rd in repos_data:
                if other_rd['repo'] == rd['repo']:
                    continue
                other_vars = _safe_set(other_rd.get('env_vars_context', {}).keys())
                common = current_vars.intersection(other_vars)
                if common:
                    cross_links.append(f"SHARED STATE: {rd['repo']} and {other_rd['repo']} share env keys: {list(common)}")

            # Grep for other repo names in this repo's configs/env (Service Discovery)
            for other in repo_names:
                if other == rd.get('repo_name_short'):
                    continue
                for path, content in rd.get('configs_summary', {}).items():
                    if other in content.lower():
                        cross_links.append(f"DEPENDENCY HINT: {rd['repo']} mentions {other} in {path} (Potential URL target)")

        try:
            cross_links_deduped = _safe_set(cross_links)
            brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(cross_links_deduped) if cross_links_deduped else ""
        except TypeError as exc:
            logger.warning("Unhashable cross_links entry: %s", exc)
            cross_links_safe = [str(x) for x in cross_links]
            cross_links_deduped = _safe_set(cross_links_safe)
            brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(cross_links_deduped) if cross_links_deduped else ""
        full_prompt = f"### ECOSYSTEM ARCHITECTURAL BRIEF\n{brief_header}\n\n"
        full_prompt += "### REPOSITORY DETAILS\n" + "\n".join(repo_summaries)

        # 6. Call AI Senate
        logger.info("=== SENDING INITIAL ANALYSIS PROMPT TO AI ===")
        logger.info(f"Provider: {ai_provider}")
        logger.info(f"Repository count: {len(repos_data)}")

        # Log the system prompt
        _log_ecosystem_prompt()

        logger.info("=== INITIAL ANALYSIS PROMPT ===")
        logger.info(f"Prompt length: {len(full_prompt)} characters")
        logger.info("Prompt preview:")
        # Show first part of prompt
        prompt_preview = full_prompt[:1000] if len(full_prompt) > 1000 else full_prompt
        logger.info(prompt_preview)
        if len(full_prompt) > 1000:
            logger.info("... [prompt truncated] ...")

        # Wrap the AI call in the same try as the parse so a "no AI providers
        # configured" RuntimeError from _cached_ask falls through to the
        # heuristic-only fallback below — same path the parser takes on a
        # malformed response. Without this, scan_and_analyze returns an
        # empty plan to the UI ("0 services for deployment") whenever the
        # operator hasn't yet wired up an LLM API key.
        try:
            from apps.intelligence.providers import _cached_ask
            response_text, provider = _cached_ask(
                full_prompt, system_prompt=ECOSYSTEM_PROMPT, provider_id=ai_provider,
            )
            response_text = response_text or ""
        except Exception:
            logger.exception("AI analysis unavailable; falling back to heuristic plan")
            response_text = ""
            provider = None

        logger.info("=== INITIAL AI RESPONSE RECEIVED ===")
        logger.info(f"Response provider: {provider}")
        logger.info(f"Response length: {len(response_text)} characters")
        logger.info("Response preview:")
        response_preview = response_text[:1000] if len(response_text) > 1000 else response_text
        logger.info(response_preview)
        if len(response_text) > 1000:
            logger.info("... [response truncated] ...")

        # 7. Parse and structure the plan (Workspace is now deleted)
        try:
            # Intelligently extract JSON block by finding the outermost braces
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')

            if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
                raise ValueError("No JSON found in Senate response")

            json_str = response_text[start_idx:end_idx+1]
            plan = json.loads(json_str)

            # Validate AI response before processing
            if not _validate_ai_response_structure(response_text):
                logger.warning("AI response validation failed, attempting revalidation...")
                raise ValueError("AI response structure validation failed")

            # Sanitize the response for safe processing
            plan = _sanitize_ai_response_for_processing(response_text)

            if isinstance(plan, dict) and isinstance(plan.get("services"), list):
                # Ensure each service is a dict with sanitized list fields
                sanitized_services = []
                for svc in plan["services"]:
                    if not isinstance(svc, dict):
                        continue
                    _normalize_service_plan_fields(svc)
                    sanitized_services.append(svc)
                plan["services"] = sanitized_services

                # Attach scanner-detected env_prefixes to each service
                for svc in plan["services"]:
                    repo = svc.get("repo", "")
                    for rd in repos_data:
                        if rd.get("repo") == repo:
                            svc["_env_prefixes"] = list(rd.get("env_prefixes", []))
                            svc["_is_heavy"] = rd.get("is_heavy", False)
                            break

                _apply_plan_repo_defaults(plan["services"], repos_data)
                # Force-merge scanner-detected env vars that the AI dropped.
                # This MUST happen before _apply_generic_ecosystem_intelligence
                # so cross-service secret mapping can find vars like GATEWAY_SECRET.
                _force_merge_scanner_env_vars(plan["services"], repos_data)
                _apply_generic_ecosystem_intelligence(plan["services"])
                _ai_env_crosscheck(plan["services"], ai_provider)
                plan["addons"] = _rebuild_addons_manifest(plan["services"], plan.get("addons", []))
                plan["deploy_sequence"] = _build_deploy_sequence(plan["services"])

            plan["ai_provider"] = provider
            return plan

        except ValueError as e:
            logger.warning(f"AI response validation failed: {e}")
            # Try to revalidate with AI
            if ai_provider:
                return _attempt_ai_revalidation(repos_data, ai_provider, str(e))
            return _build_heuristic_plan(repos_data, str(e))
        except Exception as e:
            logger.error("Failed to parse AI ecosystem response: %s", e)
            # Fall back to heuristic-only plan
            return _build_heuristic_plan(repos_data, str(e))


def analyze_ecosystem_chunked(repos_data: list[dict], github_token: str | None = None, ai_provider: str | None = None, chunk_size: int = 4, existing_services: list | None = None) -> dict:
    """
    Analyzes repos in batches of `chunk_size` to prevent token limits.
    After accumulating the partial plans, it runs a final AI synthesis pass
    to fix cross-repo links and consolidate addons.
    """

    global_services: list = []
    global_addons_map: dict = {}

    # Process in chunks
    chunks = [repos_data[i:i + chunk_size] for i in range(0, len(repos_data), chunk_size)]

    def _analyze_single_chunk(idx: int, chunk: list, token: str | None = None, provider: str | None = None):
        """Analyze a single ecosystem chunk."""
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
        except Exception:
            pass

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

    # Rebuild preliminary addons
    global_addons = [{"type": k, "shared_by": list(v)} for k, v in global_addons_map.items()]

    # Final AI Synthesis Pass if there was more than one chunk
    if len(chunks) > 1:
        # Re-build global summaries for the synthesis prompt
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

    # Strictly sanitize services: strip non-dicts, normalize each, build a clean list
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

    # === RECONCILIATION: ensure every input repo has a corresponding service ===
    # The AI may omit repos (clone failure, token limits, model truncation).
    # Fill in missing repos with heuristic-based service entries so the user
    # never sees silently-dropped repos.
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
        # Attach scanner-detected env_prefixes to each service
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


def _attempt_ai_revalidation(repos_data: list[dict], ai_provider: str, error_message: str) -> dict:
    """
    Attempt to revalidate and correct AI response when validation fails.
    """
    logger.info("=== ATTEMPTING AI REVALIDATION ===")
    logger.info(f"Error message: {error_message}")
    logger.info(f"Repository data count: {len(repos_data)}")

    # Log repository details for debugging
    for i, rd in enumerate(repos_data):
        logger.info(f"Repo {i+1}: {rd.get('repo', 'unknown')} - {rd.get('description', 'No description')}")

    try:
        revalidation_prompt = f"""
        CRITICAL: Your previous ecosystem plan was rejected due to: {error_message}

        REPOSITORY DATA:
        {json.dumps([{"repo": rd.get("repo"), "description": rd.get("description"), "stack": rd.get("stack")} for rd in repos_data], indent=2)}

        REQUIREMENTS:
        1. Return ONLY valid JSON with this exact structure:
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

        2. CRITICAL TYPE RULES:
           - ALL array fields ("depends_on", "shared_by", "addons", "deploy_sequence") must contain ONLY strings
           - "env_vars" must be a dict with string keys and string values ONLY
           - No nested objects in any array fields
           - No unhashable types (dicts, lists) in any string fields

        3. Ensure all services have proper names and repo references
        """

        logger.info("=== REVALIDATION PROMPT SENT TO AI ===")
        logger.info(f"Provider: {ai_provider}")
        logger.info(f"Prompt length: {len(revalidation_prompt)} characters")
        logger.info("Prompt preview:")
        # Show first and last parts of the prompt to avoid flooding logs
        preview_start = revalidation_prompt[:500]
        preview_end = revalidation_prompt[-500:] if len(revalidation_prompt) > 1000 else ""
        logger.info(preview_start)
        if preview_end:
            logger.info("... [truncated] ...")
            logger.info(preview_end)

        from apps.intelligence.providers import _cached_ask
        response_text, provider = _cached_ask(
            revalidation_prompt,
            system_prompt=ECOSYSTEM_PROMPT,
            provider_id=ai_provider
        )
        response_text = response_text or ""

        logger.info("=== AI REVALIDATION RESPONSE RECEIVED ===")
        logger.info(f"Response provider: {provider}")
        logger.info(f"Response length: {len(response_text)} characters")
        logger.info("Response preview:")
        # Show first part of response
        response_preview = response_text[:1000] if len(response_text) > 1000 else response_text
        logger.info(response_preview)
        if len(response_text) > 1000:
            logger.info("... [response truncated] ...")

        # Validate the revalidated response
        logger.info("=== VALIDATING REVALIDATED RESPONSE ===")
        is_valid = _validate_ai_response_structure(response_text)
        logger.info(f"Revalidation validation result: {is_valid}")

        if is_valid:
            plan = _sanitize_ai_response_for_processing(response_text)
            logger.info("=== AI REVALIDATION SUCCESSFUL ===")
            logger.info(f"Plan contains {len(plan.get('services', []))} services")
            logger.info(f"Plan contains {len(plan.get('addons', []))} addons")
            return plan
        else:
            logger.error("=== AI REVALIDATION FAILED ===")
            logger.error("Revalidation response validation failed after AI correction")
            return _build_heuristic_plan(repos_data, "AI response structure validation failed after revalidation")

    except Exception as e:
        logger.error("=== AI REVALIDATION PROCESS FAILED ===")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {e!s}")
        logger.error(f"Error details: {e}")
        return _build_heuristic_plan(repos_data, f"AI revalidation failed: {e!s}")
