import concurrent.futures
import hashlib
import os

from django.core.cache import cache

from .base import AIProvider, _is_circuit_open, _record_provider_failure, logger
from .sync import _sync_db_to_env
from .registry import get_configured_providers, PROVIDERS, _resolve_providers

COMMITTEE_SYSTEM_PROMPT = (
    "You are a member of the SMSLY AI Senate Committee — a panel of AI experts "
    "that collaborates on DevOps, deployment, and infrastructure decisions. "
    "You provide honest, technical analysis. When reviewing peers, be constructive "
    "but direct about disagreements."
)

CODE_REVIEW_SYSTEM_PROMPT = (
    "You are an expert code reviewer. Analyze the provided code thoroughly.\n"
    "Focus on:\n"
    "1. Bugs, errors, and potential issues\n"
    "2. Security vulnerabilities\n"
    "3. Performance problems\n"
    "4. Code quality and best practices\n"
    "5. Missing error handling\n\n"
    "Be specific with line references. Provide actionable feedback."
)

SENATE_COMMITTEE_COST_MULTIPLIER = 3


def _ask_single(
    provider: AIProvider,
    prompt: str,
    system_prompt: str | None = None
) -> tuple[str, str]:
    """Ask a single provider. Returns (response, provider_name) or raises."""
    response = provider.ask(prompt, system_prompt=system_prompt)
    return response, provider.name()


def _parallel_ask(providers: list[AIProvider], prompt: str,
                  system_prompt: str | None = None) -> list[tuple[str, str]]:
    """Ask multiple providers in parallel. Returns list of (response, name)."""
    results: list[tuple[str, str]] = []

    timeout = int(os.environ.get("SENATE_TIMEOUT_SECONDS", "180"))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(providers))
    futures = {
        pool.submit(_ask_single, p, prompt, system_prompt): p
        for p in providers
    }
    try:
        for future in concurrent.futures.as_completed(futures, timeout=timeout):
            provider = futures[future]
            try:
                response, name = future.result()
                results.append((response, name))
            except Exception as e:
                _record_provider_failure(getattr(provider, "id", ""))
                logger.warning("Provider %s failed: %s", provider.name(), e)
    except concurrent.futures.TimeoutError:
        logger.warning("Parallel ask timed out, proceeding with %d results", len(results))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return results


def ask_collaborative(prompt: str, system_prompt: str | None = None) -> tuple[str, str]:
    _sync_db_to_env()
    configured = [p for p in get_configured_providers() if not _is_circuit_open(getattr(p, "id", ""))]

    if not configured:
        raise RuntimeError("No AI providers configured. Add an API key in Settings > AI.")

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _ask_single(provider, prompt, system_prompt)
        except Exception as e:
            _record_provider_failure(getattr(provider, "id", ""))
            logger.warning("Single provider %s failed: %s", provider.name(), e)
            raise

    logger.info("Senate Committee convened with %d members", len(configured))
    proposals = _parallel_ask(configured, prompt, system_prompt or COMMITTEE_SYSTEM_PROMPT)

    if not proposals:
        raise RuntimeError(f"All {len(configured)} AI providers failed to respond.")

    if len(proposals) == 1:
        return proposals[0]

    member_names = [name for _, name in proposals]
    logger.info("Phase 1 complete: %d proposals received from %s",
                len(proposals), ", ".join(member_names))

    proposals_text = "\n\n---\n\n".join(
        f"### Proposal by {name}\n{resp}" for resp, name in proposals
    )

    review_prompt = (
        f"You are reviewing {len(proposals)} proposals from fellow committee members.\n\n"
        f"Original question:\n{prompt}\n\n"
        f"Proposals:\n{proposals_text}\n\n"
        f"For EACH proposal, vote:\n"
        f"- **AGREE** — the proposal is correct and complete\n"
        f"- **AMEND** — partially correct but needs changes (explain what)\n"
        f"- **DISAGREE** — fundamentally wrong (explain why)\n\n"
        f"Then state your FINAL RECOMMENDATION in 2-3 sentences.\n"
        f"Format: one vote per proposal, then your recommendation."
    )

    votes = _parallel_ask(configured, review_prompt, COMMITTEE_SYSTEM_PROMPT)
    logger.info("Phase 2 complete: %d votes received", len(votes))

    votes_text = "\n\n---\n\n".join(
        f"### Review by {name}\n{resp}" for resp, name in votes
    ) if votes else "No reviews were submitted."

    chair_prompt = (
        f"You are the CHAIR of the SMSLY AI Senate Committee.\n\n"
        f"Original question:\n{prompt}\n\n"
        f"Proposals submitted:\n{proposals_text}\n\n"
        f"Committee votes and reviews:\n{votes_text}\n\n"
        f"Write the FINAL COMMITTEE RESOLUTION:\n"
        f"1. State the consensus answer (what the majority agreed on)\n"
        f"2. Note any important dissenting points\n"
        f"3. Give the final actionable recommendation\n\n"
        f"Be concise — max 250 words. Write as the committee, not as an individual."
    )

    chair = configured[1] if len(configured) > 1 else configured[0]
    try:
        resolution = chair.ask(chair_prompt, system_prompt=COMMITTEE_SYSTEM_PROMPT)
        attribution = f"Senate Committee ({' + '.join(member_names)})"
        logger.info("Phase 3 complete: Chair %s delivered resolution", chair.name())
        return resolution, attribution
    except Exception as e:
        _record_provider_failure(getattr(chair, "id", ""))
        logger.warning("Chair %s failed to deliver resolution: %s", chair.name(), e)
        for fallback_chair in configured:
            if fallback_chair is not chair:
                try:
                    resolution = fallback_chair.ask(
                        chair_prompt, system_prompt=COMMITTEE_SYSTEM_PROMPT
                    )
                    attribution = f"Senate Committee ({' + '.join(member_names)})"
                    return resolution, attribution
                except Exception:
                    _record_provider_failure(getattr(fallback_chair, "id", ""))
                    continue
        return proposals[0][0], f"{proposals[0][1]} (committee failed, solo answer)"


_ask_code_review_depth = [0]


def ask_code_review(
    prompt: str,
    system_prompt: str | None = None,
    provider_ids: list[str] | None = None,
) -> tuple[str, str]:
    _sync_db_to_env()

    if not provider_ids or len(provider_ids) < 2:
        return ask_with_fallback(prompt, system_prompt)

    _ask_code_review_depth[0] += 1
    try:
        if _ask_code_review_depth[0] > 3:
            logger.warning("ask_code_review recursion limit reached")
            return "", "code-review(recursion_limit)"

        available = _resolve_providers(provider_ids[:2])
        if len(available) < 2:
            if not any(pid for pid in (provider_ids or [])):
                return "", "code-review(no_providers)"
            return ask_with_fallback(prompt, system_prompt, provider_ids[0] if provider_ids else None)

        agent_a, agent_b = available[0], available[1]
        effective_system = system_prompt or CODE_REVIEW_SYSTEM_PROMPT

        phase1_prompt = (
            f"You are performing a thorough code review.\n"
            f"Analyze the following and provide your assessment:\n\n{prompt}"
        )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            future_a = pool.submit(agent_a.ask, phase1_prompt, effective_system)
            future_b = pool.submit(agent_b.ask, phase1_prompt, effective_system)

            review_a = future_a.result(timeout=60)
            review_b = future_b.result(timeout=60)
        except Exception as exc:
            pool.shutdown(wait=False, cancel_futures=True)
            logger.warning("Code review phase 1 failed: %s", exc)
            return ask_with_fallback(prompt, system_prompt)

        cross_prompt_a = (
            f"You previously reviewed code and provided:\n---\n{review_a}\n---\n\n"
            f"Another agent reviewed the same code and provided:\n---\n{review_b}\n---\n\n"
            f"Now provide your FINAL assessment. Consider both perspectives.\n"
            f"Identify any issues the other agent caught that you missed.\n"
            f"Resolve any disagreements with reasoning.\n\n"
            f"Original code/task:\n{prompt}"
        )

        cross_prompt_b = (
            f"You previously reviewed code and provided:\n---\n{review_b}\n---\n\n"
            f"Another agent reviewed the same code and provided:\n---\n{review_a}\n---\n\n"
            f"Now provide your FINAL assessment. Consider both perspectives.\n"
            f"Identify any issues the other agent caught that you missed.\n"
            f"Resolve any disagreements with reasoning.\n\n"
            f"Original code/task:\n{prompt}"
        )

        try:
            future_cross_a = pool.submit(agent_a.ask, cross_prompt_a, effective_system)
            future_cross_b = pool.submit(agent_b.ask, cross_prompt_b, effective_system)

            final_a = future_cross_a.result(timeout=60)
            final_b = future_cross_b.result(timeout=60)
        except Exception as exc:
            pool.shutdown(wait=False, cancel_futures=True)
            logger.warning("Code review phase 2 failed: %s", exc)
            combined = f"## Agent A Review:\n{review_a}\n\n## Agent B Review:\n{review_b}"
            return combined, f"code-review({agent_a.id},{agent_b.id})"
        finally:
            pool.shutdown(wait=False)

        combined = (
            f"## Code Review: {agent_a.id} + {agent_b.id}\n\n"
            f"### Agent A ({agent_a.id}) Final Assessment:\n{final_a}\n\n"
            f"### Agent B ({agent_b.id}) Final Assessment:\n{final_b}\n"
        )

        return combined, f"code-review({agent_a.id},{agent_b.id})"
    finally:
        _ask_code_review_depth[0] -= 1


def ask_with_fallback(
    prompt: str,
    system_prompt: str | None = None,
    provider_id: str | None = None,
    mode: str = "auto",
    return_usage: bool = False,
) -> tuple[str, str]:
    _sync_db_to_env()
    configured = [p for p in get_configured_providers() if not _is_circuit_open(getattr(p, "id", ""))]

    def _wrap(resp: str, name: str):
        if return_usage:
            return resp, name, {}
        return resp, name

    if mode == "code_review" and len(configured) >= 2:
        try:
            result = ask_code_review(
                prompt, system_prompt,
                [p.id for p in configured[:2]],
            )
            if result and result[0]:
                return _wrap(*result)
        except Exception as exc:
            logger.warning("ask_code_review failed, falling back to direct: %s", exc)
        for provider in configured:
            try:
                return _wrap(*_ask_single(provider, prompt, system_prompt))
            except Exception as exc:
                _record_provider_failure(getattr(provider, "id", ""))
                logger.warning("Direct provider rescue with %s failed: %s", provider.name(), exc)
        raise RuntimeError("All configured AI providers failed to respond.")

    if mode == "auto" and len(configured) == 2:
        try:
            result = ask_code_review(
                prompt, system_prompt,
                [p.id for p in configured[:2]],
            )
            if result and result[0]:
                return _wrap(*result)
        except Exception as exc:
            logger.warning("ask_code_review failed, falling back to direct: %s", exc)
        for provider in configured:
            try:
                return _wrap(*_ask_single(provider, prompt, system_prompt))
            except Exception as exc:
                _record_provider_failure(getattr(provider, "id", ""))
                logger.warning("Direct provider rescue with %s failed: %s", provider.name(), exc)
        raise RuntimeError("All configured AI providers failed to respond.")

    if provider_id and provider_id != "auto":
        target = next((p for p in configured if getattr(p, "id", "") == provider_id or p.__class__.__name__.lower().startswith(provider_id.lower())), None)
        if not target:
            cls = PROVIDERS.get(provider_id)
            if cls:
                instance = cls()
                if instance.is_configured():
                    target = instance

        if target:
            try:
                return _wrap(*_ask_single(target, prompt, system_prompt))
            except Exception as e:
                _record_provider_failure(provider_id)
                logger.warning("Target provider %s failed, falling back: %s", provider_id, e)

    senate_enabled = os.environ.get("SENATE_ENABLED", "True").lower() == "true"
    if len(configured) >= 2 and senate_enabled:
        try:
            return _wrap(*ask_collaborative(prompt, system_prompt))
        except Exception as exc:
            logger.warning("ask_collaborative failed: %s", exc)

        for provider in configured:
            try:
                return _wrap(*_ask_single(provider, prompt, system_prompt))
            except Exception as exc:
                _record_provider_failure(getattr(provider, "id", ""))
                logger.warning("Committee rescue with %s failed: %s", provider.name(), exc)
        raise RuntimeError("All configured AI providers failed to respond.")

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _wrap(*_ask_single(provider, prompt, system_prompt))
        except Exception as e:
            _record_provider_failure(getattr(provider, "id", ""))
            raise RuntimeError(f"AI provider {provider.name()} failed: {e}")

    raise RuntimeError("No AI providers configured. Add an API key in Settings > AI.")


def _cached_ask(
    prompt: str,
    system_prompt: str | None = None,
    provider_id: str | None = None,
    ttl: int = 600,
    cache_bypass: bool = False,
    mode: str = "auto",
    return_usage: bool = False,
) -> tuple:
    if cache_bypass:
        return ask_with_fallback(
            prompt, system_prompt, provider_id, mode=mode, return_usage=return_usage,
        )

    cache_input = f"{system_prompt or ''}:{prompt}"
    cache_hash = hashlib.sha256(cache_input.encode()).hexdigest()[:20]
    cache_key = f"ai:response:{cache_hash}"

    cached = cache.get(cache_key)
    if cached is not None:
        if return_usage and len(cached) == 2:
            return cached[0], cached[1], {}
        return cached

    result = ask_with_fallback(
        prompt, system_prompt, provider_id, mode=mode, return_usage=return_usage,
    )

    if return_usage and len(result) == 3:
        cache.set(cache_key, (result[0], result[1]), timeout=ttl)
    else:
        cache.set(cache_key, result, timeout=ttl)

    return result
