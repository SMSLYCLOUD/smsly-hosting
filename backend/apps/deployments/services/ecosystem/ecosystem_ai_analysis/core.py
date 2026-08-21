import json
import logging
import os
import tempfile

from ..ecosystem_heuristics import _safe_set
from ..ecosystem_ai_prompts import ECOSYSTEM_PROMPT, _log_ecosystem_prompt
from ..ecosystem_validation import _validate_ai_response_structure, _sanitize_ai_response_for_processing
from ..ecosystem_intelligence import (
    _normalize_service_plan_fields, _apply_plan_repo_defaults, _apply_generic_ecosystem_intelligence,
    _ai_env_crosscheck, _rebuild_addons_manifest, _build_deploy_sequence,
)
from ..ecosystem_pipeline import _force_merge_scanner_env_vars, _build_heuristic_plan

logger = logging.getLogger(__name__)


def analyze_ecosystem(repos_data: list[dict], github_token: str | None = None, ai_provider: str | None = None, existing_services: list | None = None) -> dict:
    """
    Use AI Senate to analyze all repos together in a temporary workspace.
    Clones repos, scans for cross-repo dependencies, and produces a plan.
    """

    with tempfile.TemporaryDirectory(prefix="cloud-ecosystem-") as workspace_dir:
        logger.info(f"Created ecosystem workspace: {workspace_dir}")

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

            from ..ecosystem_github import _clone_repo
            success = _clone_repo(repo_full, target_dir, github_token)
            if success:
                rd['clone_dir'] = target_dir
                rd['repo_name_short'] = repo_name
            else:
                logger.warning(f"Failed to clone {repo_full} for analysis")

        for rd in repos_data:
            clone_dir = rd.get('clone_dir')
            if clone_dir:
                from apps.intelligence.scanner import RepoScanner
                scan_depth = rd.get('scan_depth', 'shallow')
                scanner = RepoScanner(clone_dir, scan_depth=scan_depth)
                scan = scanner.scan()
                rd['env_vars_context'] = scan.get('env_vars_context', {})
                rd['env_prefixes'] = scan.get('env_prefixes', [])
                rd['stack'] = scan.get('stack', rd.get('stack', 'unknown'))

                configs_summary = {}
                priority_files = ['docker-compose.yml', 'docker-compose.yaml', 'Dockerfile', 'package.json', 'requirements.txt', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'config.py', 'settings.py', 'main.py', 'app.py']

                raw_configs = scan.get('configs', {})
                critical_configs = [(k, v) for k, v in raw_configs.items() if any(p in os.path.basename(k) for p in priority_files)]

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
                        lines = [line for line in v.split('\n') if line.strip() and not line.strip().startswith('#')]
                        configs_summary[k] = '\n'.join(lines)[:800]
                    else:
                        configs_summary[k] = v[:300] + "\n...[truncated]"

                rd['configs_summary'] = configs_summary
                rd['structure'] = scan.get('structure', '')

        repo_summaries = []
        for rd in repos_data:
            summary = f"\n### REPO: {rd['repo']} (Name: {rd.get('repo_name_short', 'unknown')})\n"
            summary += f"Description: {rd.get('description', 'No description')}\n"
            summary += f"Stack: {rd.get('stack', 'unknown')}\n"

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

        logger.info("=== SENDING INITIAL ANALYSIS PROMPT TO AI ===")
        logger.info(f"Provider: {ai_provider}")
        logger.info(f"Repository count: {len(repos_data)}")

        _log_ecosystem_prompt()

        logger.info("=== INITIAL ANALYSIS PROMPT ===")
        logger.info(f"Prompt length: {len(full_prompt)} characters")
        logger.info("Prompt preview:")
        prompt_preview = full_prompt[:1000] if len(full_prompt) > 1000 else full_prompt
        logger.info(prompt_preview)
        if len(full_prompt) > 1000:
            logger.info("... [prompt truncated] ...")

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

        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')

            if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
                raise ValueError("No JSON found in Senate response")

            json_str = response_text[start_idx:end_idx+1]
            plan = json.loads(json_str)

            if not _validate_ai_response_structure(response_text):
                logger.warning("AI response validation failed, attempting revalidation...")
                raise ValueError("AI response structure validation failed")

            plan = _sanitize_ai_response_for_processing(response_text)

            if isinstance(plan, dict) and isinstance(plan.get("services"), list):
                sanitized_services = []
                for svc in plan["services"]:
                    if not isinstance(svc, dict):
                        continue
                    _normalize_service_plan_fields(svc)
                    sanitized_services.append(svc)
                plan["services"] = sanitized_services

                for svc in plan["services"]:
                    repo = svc.get("repo", "")
                    for rd in repos_data:
                        if rd.get("repo") == repo:
                            svc["_env_prefixes"] = list(rd.get("env_prefixes", []))
                            svc["_is_heavy"] = rd.get("is_heavy", False)
                            break

                _apply_plan_repo_defaults(plan["services"], repos_data)
                _force_merge_scanner_env_vars(plan["services"], repos_data)
                _apply_generic_ecosystem_intelligence(plan["services"])
                _ai_env_crosscheck(plan["services"], ai_provider)
                plan["addons"] = _rebuild_addons_manifest(plan["services"], plan.get("addons", []))
                plan["deploy_sequence"] = _build_deploy_sequence(plan["services"])

            plan["ai_provider"] = provider
            return plan

        except ValueError as e:
            logger.warning(f"AI response validation failed: {e}")
            if ai_provider:
                return _attempt_ai_revalidation(repos_data, ai_provider, str(e))
            return _build_heuristic_plan(repos_data, str(e))
        except Exception as e:
            logger.error("Failed to parse AI ecosystem response: %s", e)
            return _build_heuristic_plan(repos_data, str(e))


def _attempt_ai_revalidation(repos_data: list[dict], ai_provider: str, error_message: str) -> dict:
    logger.info("=== ATTEMPTING AI REVALIDATION ===")
    logger.info(f"Error message: {error_message}")
    logger.info(f"Repository data count: {len(repos_data)}")

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
        response_preview = response_text[:1000] if len(response_text) > 1000 else response_text
        logger.info(response_preview)
        if len(response_text) > 1000:
            logger.info("... [response truncated] ...")

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
