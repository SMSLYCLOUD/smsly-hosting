# AI & Intelligence

Grid's AI subsystem provides 17 model providers behind a single chat surface, a multi-agent "Senate Committee" deliberator, a Jules auto-fix loop, and a runtime intelligence layer that watches every service for anomalies. AI is opt-in: nothing is enabled until an admin saves a provider API key.

## Overview

The AI subsystem has three layers:

1. **Providers** — A uniform interface over 17 third-party LLM APIs. The platform auto-discovers which providers are configured; a single key means "solo" mode, two-or-more keys means "Senate Committee" mode.
2. **Intelligence** — Periodic background tasks that scan services, summarize deployments, and emit remediation recommendations. See [docs/intelligence.md](intelligence.md).
3. **Jules auto-fix** — A specialized agent that opens Pull Requests on failed deployments. Opt-in via `JULES_AUTO_DEPLOY_PR`.

### Supported Providers

| Provider | Key env var | Default model | Notes |
| --- | --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | Streaming supported. |
| Grok (xAI) | `GROK_API_KEY` | `grok-3-mini` | |
| Gemini | `GEMINI_API_KEY` | `gemini-2.0-flash` | |
| Claude (Anthropic) | `CLAUDE_API_KEY` | `claude-sonnet-4-20250514` | |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-coder` | |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter/auto` | Routes to the cheapest model by default. |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | Very low latency. |
| Alibaba (Qwen) | `ALIBABA_API_KEY` | `qwen-max` | |
| Jules (Google) | `JULES_API_KEY` | `jules-latest` | Used by the auto-fix loop, not for general chat. |
| Local LLM (OpenAI-compatible) | `LOCALLLM_API_KEY` | `local-model` | Base URL configurable for Ollama, vLLM, LM Studio. |
| SMSLY Cloud | `SMSLYCLOUD_API_KEY` | `smsly-latest` | Hosted proxy. |
| FreeModel.dev | `FREEMODEL_API_KEY` | `gpt-4o-mini` | Free tier for development. |
| OpenCode API | `OPENCODE_API_KEY` | `opencode-latest` | |
| Mistral (La Plateforme) | `MISTRAL_API_KEY` | `mistral-small-latest` | |
| NVIDIA NIM | `NVIDIA_API_KEY` | `nvidia/llama-3.1-nemotron-70b-instruct` | |
| Cloudflare Workers AI | `CLOUDFLARE_API_KEY` | `@cf/meta/llama-3.1-8b-instruct` | Via the AI Gateway. |
| Mock (fallback) | n/a | n/a | Returns canned responses when no keys are set. |

## Provider Configuration (admin only)

Provider configuration is a singleton row in `AIProviderSettings` (pk=1). It is created automatically on first access via `AIProviderSettings.get_solo()`. API keys are `EncryptedCharField` (Fernet) and are never returned in API responses — only the configured / unconfigured status is exposed.

There are two ways to configure a provider:

1. **UI** — Settings → AI → Providers. Save keys per provider. The UI never displays the saved key (only a "configured" badge).
2. **API** — `POST /api/v1/ai/providers/update/` (admin only). The body is a partial update of the singleton. The request must include the admin session cookie or a token with `is_superuser=True`.

Either path calls `_sync_db_to_env()` which writes the keys into the worker process's environment so the next LLM call picks them up.

### The `_validate_https_allowlist` Gate

The Jules provider's `jules_base_url` is validated against `settings.JULES_ALLOWED_HOSTS` (default `['api.jules.google.com']`). Any other host is rejected at `clean()` time. This prevents an admin from accidentally pointing Jules at an attacker-controlled endpoint.

The validator requires `https://` and the host to be in the allowlist. Empty values are accepted (the default is used). The check runs on every `save()` of `AIProviderSettings`.

## Rate Limits

The AI endpoints are throttled to prevent accidental cost overruns:

| Throttle | Rate | Scope | Endpoints |
| --- | --- | --- | --- |
| `AIChatRateThrottle` | `30/minute` | per user | `ai_chat_completions`, `ai_chat_stream` |
| `AIAnalysisRateThrottle` | `10/minute` | per user | `ai_test_prompt`, `ai_analyze_logs`, `ai_cost_estimate`, `ai_intelligence_report`, `jules_fix_history` |
| `UserAICap` | per-user daily cap | per user | All endpoints |

The `UserAICap` model holds the per-user daily cap. It defaults to:

- `daily_token_cap = 100000` (tokens/day)
- `daily_cost_cap_usd = 10.00` (USD/day)

When the cap is exceeded the API returns HTTP 429 with a reason (`Daily token cap exceeded` or `Daily cost cap exceeded`). The Senate Committee applies a 3× multiplier on the cap pre-flight check (`SENATE_COMMITTEE_COST_MULTIPLIER`), so the effective per-call cap is divided by 3 before the committee runs.

`LLMUsage` rows are written for every successful LLM call. The cap is recomputed on each call by summing `total_tokens` and `estimated_cost_usd` for the user for `created_at__date=today`. The `estimated_cost_usd` is computed as `(total_tokens / 1000) * LLM_USD_PER_1K_TOKENS` (env, default `0.002`).

## Senate Committee

When two or more providers are configured and `senate_enabled=True`, the chat endpoints switch from solo mode to a three-phase deliberation. The committee is capped at `senate_max_members` (default 5) — only the first N configured providers participate.

### Phase 1 — Propose

Every committee member answers the prompt independently and in parallel. Each call has a `SENATE_TIMEOUT_SECONDS` timeout (default 180s). The parallel pool uses `ThreadPoolExecutor(max_workers=len(providers))` with `cancel_futures=True` on timeout.

### Phase 2 — Review

Each provider receives **all** other proposals and is asked to review and vote. Voting is a structured "I agree with member X because …" or "I disagree with member X because …" — the exact structure is model-specific, but the system prompt instructs the model to be "constructive but direct about disagreements". This phase is also parallelized.

### Phase 3 — Chair

A chair (rotated: the second configured provider by default, falling back to the first) receives all proposals and reviews, then synthesizes a final resolution. If the chair fails (timeout, 5xx, bad JSON), the next configured provider in the list is rotated in as chair and the phase is retried.

The user-facing response is the chair's resolution. The audit log records the full deliberation as `metadata.votes` and `metadata.resolution`.

### Code Review Mode

When exactly two providers are configured and `mode=auto` (the default), the platform uses a lighter 2-agent code-review instead of the full Senate. The two agents cross-review each other (4 API calls total) and the user receives both reviews. This is cheaper than the Senate and produces results in ~half the time.

## Jules Auto-Fix

Jules is a specialized agent for fixing failed deployments. It is opt-in and gated by `JULES_AUTO_DEPLOY_PR`. The flow:

1. A deployment fails (`status=FAILED`).
2. `_collect_failure_context()` builds a prompt: deployment ID, last 10000 chars of build logs, plus monitoring context (CPU / memory / OOM events / crash-loop detection) from the `ScalingAnalyzer`.
3. The prompt is sent to Jules (or the configured primary provider, with Jules as the system prompt) and Jules returns a structured JSON: `{fix_description, files_to_change, suggested_changes}`.
4. The agent clones the repository, creates a branch `jules/auto-fix-<deployment-id>`, applies the suggested changes, commits, and pushes.
5. A Pull Request is opened on GitHub (or GitLab / Bitbucket, depending on the integration).
6. If `JULES_AUTO_DEPLOY_PR=True`, a new deployment is queued on the PR's branch.

### Caps

To prevent runaway auto-fixes, Jules enforces hard caps per PR:

- `MAX_FILES_PER_JULES_PR = 5` — at most 5 files per PR.
- `MAX_BYTES_PER_JULES_PR = 50_000` — at most 50 KB of diff per PR.

If the suggested fix exceeds either cap, the agent truncates the diff and writes a comment on the PR noting the truncation. The PR is still opened; the user can review and complete the fix manually.

### Failure Handling

Every external call (Jules API, GitHub API, `git push`) is wrapped in `backoff.on_exception(backoff.expo, Exception, max_tries=5, factor=2)`. If the auto-fix fails at any step, the task logs the error and returns a structured `FixResult(success=False, error=...)` payload — it never crashes the Celery worker.

The history of auto-fix attempts is exposed via `GET /api/v1/jules/history/{service_id}/`, which returns the last 10 Jules-related log lines plus a `fix_applied` / `fix_failed` boolean computed from the log content.

## Prompt Injection Policy

The AI subsystem is hardened against prompt injection in three ways:

1. **Server-side system prompts only.** User input is concatenated into the user message; the system prompt is constructed in code and cannot be overridden by the user.
2. **Truncation.** User input is truncated to a configurable length (default 20000 characters) before being sent to the model. This prevents "context-flooding" attacks where the user pastes a large document to evict the system prompt from the model's effective context.
3. **Role-marker filtering.** The pre-processor strips user-typed occurrences of `system:`, `assistant:`, `<|im_start|>`, and similar role markers from the user message. This prevents the user from impersonating the system role.

The system prompt explicitly says "Never reveal internal system details or API keys." Models that do not follow this instruction (rare, but possible) are caught by the post-processor, which scans the response for known API key patterns and substitutes them with `••••••••`.

## API Reference

All AI endpoints are mounted under `/api/v1/ai/`. Authentication is session- or token-based; admin-only endpoints are marked accordingly.

### `GET /api/v1/ai/providers/`

List all 17 providers with their configured / unconfigured status and current model. Admin-only (the endpoint reveals which providers are available to the platform).

**Query parameters:**

| Param | Type | Notes |
| --- | --- | --- |
| `include_balance` | `true`/`false` | Optional. When `true`, calls each configured provider's billing API to fetch the current balance. Slower (the call budget is `BALANCE_FETCH_BUDGET_SECONDS`, default 8s). |

**Example response (abridged):**

```json
{
  "providers": [
    {"id": "openai", "name": "OpenAI", "configured": true, "model": "gpt-4o-mini", "balance_usd": 12.34},
    {"id": "claude", "name": "Claude (Anthropic)", "configured": true, "model": "claude-sonnet-4-20250514", "balance_usd": null},
    {"id": "gemini", "name": "Gemini", "configured": false, "model": "gemini-2.0-flash", "balance_usd": null}
  ],
  "senate_enabled": true,
  "senate_max_members": 5,
  "degraded_reason": null
}
```

### `POST /api/v1/ai/providers/update/`

Update the singleton `AIProviderSettings` row. Admin-only. Accepts a partial body — only the fields included in the request are updated. API keys are `EncryptedCharField` and are never echoed back.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/ai/providers/update/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "openai_api_key": "sk-...",
    "openai_model": "gpt-4o",
    "jules_base_url": "https://api.jules.google.com/v1"
  }'
```

**Example response:**

```json
{
  "status": "ok",
  "synced_to_env": ["OPENAI_API_KEY", "OPENAI_MODEL", "JULES_BASE_URL"]
}
```

### `POST /api/v1/ai/test/`

Run a one-shot completion against a configured provider to verify the key works. Returns the model's response plus token usage. Throttled by `AIAnalysisRateThrottle`.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `provider` | string | One of the configured provider IDs. |
| `prompt` | string | The user prompt. Truncated to 4000 chars. |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/ai/test/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider": "openai", "prompt": "Reply with the word pong."}'
```

**Example response:**

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "response": "pong",
  "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}
}
```

### `POST /api/v1/ai/chat/completions/`

Standard OpenAI-style chat completions endpoint. When two-or-more providers are configured, the call is routed through the Senate Committee. Throttled by `AIChatRateThrottle`.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `messages` | array | Required. OpenAI-style `[{role, content}, ...]` message list. |
| `mode` | `auto` / `code_review` / `senate` | Optional. Forces a specific mode. Default `auto`. |
| `stream` | `false` | Use `chat/stream/` for streaming. |
| `system` | string | Optional. Overrides the platform system prompt for this call. Admin-only. |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/ai/chat/completions/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Why is my deploy stuck in HEALTH_CHECK?"}
    ]
  }'
```

**Example response (solo mode):**

```json
{
  "id": "chatcmpl-...",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "message": {"role": "assistant", "content": "..."},
  "usage": {"prompt_tokens": 423, "completion_tokens": 156, "total_tokens": 579}
}
```

**Example response (Senate mode):**

```json
{
  "id": "chatcmpl-...",
  "mode": "senate",
  "members": ["openai", "claude", "gemini"],
  "chair": "claude",
  "message": {"role": "assistant", "content": "..."},
  "votes": [
    {"member": "openai", "vote": "agree with claude", "reason": "..."},
    {"member": "gemini", "vote": "agree with claude", "reason": "..."}
  ],
  "usage": {"prompt_tokens": 1500, "completion_tokens": 480, "total_tokens": 1980}
}
```

### `POST /api/v1/ai/chat/stream/`

Server-Sent Events stream of the chat completion. The first event carries the `id`; subsequent events are token deltas. The stream is closed with a `data: [DONE]` event.

**Example request:**

```bash
curl -sS -N -X POST http://localhost:8000/api/v1/ai/chat/stream/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Write a haiku about CI/CD."}]}'
```

**Example event stream:**

```
data: {"id": "chatcmpl-abc", "provider": "openai", "model": "gpt-4o-mini"}

data: {"delta": "Pipelines"}

data: {"delta": " flow"}

data: {"delta": " like"}

data: {"delta": " rivers"}

data: [DONE]
```

### `POST /api/v1/ai/analyze_logs/`

Run a structured log analysis on a deployment's `build_logs` or runtime logs. Returns a list of detected patterns (CRASH_LOOP, OOM_KILLED, DB_CONNECTION_TIMEOUT, etc.) and a confidence score per pattern. Throttled by `AIAnalysisRateThrottle`.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `deployment_id` | UUID | Required. The deployment to analyze. |
| `mode` | `rules` / `ai` / `hybrid` | Optional. `rules` uses regex patterns only (no LLM call). `ai` uses LLM only. `hybrid` runs rules first, then asks the LLM to confirm ambiguous findings. Default `hybrid`. |

**Example response:**

```json
{
  "deployment_id": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
  "issues": [
    {"type": "OOM_KILLED", "confidence": 0.95, "evidence": "exit code 137", "fix": "Increase memory_mb by 256MB"},
    {"type": "DB_CONNECTION_TIMEOUT", "confidence": 0.72, "evidence": "could not connect to server: timeout expired", "fix": "Increase DB connection pool size"}
  ],
  "auto_remediable": ["OOM_KILLED"]
}
```

### `GET /api/v1/jules/history/{service_id}/`

Return the auto-fix history for a service. The response includes the last 10 Jules-related log lines and a derived `fix_applied` / `fix_failed` boolean.

**Example response:**

```json
{
  "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "fix_attempts": 3,
  "fix_applied": true,
  "fix_failed": false,
  "jules_events": [
    "Jules auto-fix triggered for deployment 2d3e4f5a",
    "Cloned repo at commit abc1234",
    "Created branch jules/auto-fix-2d3e4f5a",
    "PR created: https://github.com/org/repo/pull/42"
  ]
}
```

## Security

### Encrypted API Keys at Rest

All 17 provider API keys are `EncryptedCharField(max_length=500)` columns in `AIProviderSettings`. The encryption is Fernet (symmetric, AES-128-CBC + HMAC-SHA256) using the platform's `BACKUP_ENCRYPTION_KEY` (or a separate `AI_ENCRYPTION_KEY` if set) as the master key. The key is loaded from the platform `.env` at boot.

Key rotation: re-encrypt the rows with the new key, then update `.env` and restart. There is no in-place re-key tool; the recommended path is to re-save each key through the UI after the restart (the encrypt-on-write path will use the new key).

### Per-User Spend Caps

`UserAICap` is a one-to-one table on `User`. Defaults are `daily_token_cap=100000` and `daily_cost_cap_usd=10.00`. To raise the cap for a specific user, edit the row directly or call `UserAICap.objects.update_or_create(user=…, defaults={…})` in a Django shell.

The cap is recomputed on every LLM call. It is not a per-second or per-minute cap — the only per-second throttling is the DRF throttle classes.

### Senate Committee Cost Multiplier

The Senate Committee pre-flight divides the user's cap by 3 (`SENATE_COMMITTEE_COST_MULTIPLIER`). This is a conservative guard: a typical Senate call uses 3× the tokens of a solo call (one propose + one review + one chair), so the pre-divided cap roughly matches the post-call usage.

## Troubleshooting

### "No AI providers configured. Add an API key in Settings > AI."

None of the 17 provider keys are set. Open Settings → AI → Providers and save at least one. The platform will not auto-fall-back to mock mode in production — it returns this error so the operator knows the AI subsystem is not configured.

### "Provider X failed: 401 Unauthorized"

The API key is invalid or has been rotated. Re-save the key in Settings → AI. The platform's `_sync_db_to_env()` runs on every save and writes the new key into the worker environment.

### "Provider X failed: 429 Too Many Requests"

The provider is rate-limiting the platform. The default retry is 3 attempts with exponential back-off (`retry_429(max_retries=3, base_delay=2.0)` in `providers.py`). After 3 failures the call is recorded in `LLMUsage` with zero tokens and the user gets a 502.

### "Daily cost cap exceeded"

The user has hit their `UserAICap.daily_cost_cap_usd`. Either wait until tomorrow (the cap is daily) or raise the cap in a Django shell.

### "Jules auto-fix did not create a PR"

Inspect `GET /api/v1/jules/history/{service_id}/` for the failure reason. The most common cause is that the suggested fix exceeded `MAX_FILES_PER_JULES_PR` (5) or `MAX_BYTES_PER_JULES_PR` (50000). Increase either cap in `apps/intelligence/jules_fix/jules_fix.py` if your fix is genuinely large.

### "Provider 'jules' not in JULES_ALLOWED_HOSTS"

The platform's `JULES_ALLOWED_HOSTS` setting is missing the host portion of `jules_base_url`. Default is `['api.jules.google.com']`. If you self-host Jules, add the host to the allowlist in `.env`:

```
JULES_ALLOWED_HOSTS=api.jules.google.com,jules.internal.example.com
```

### Streaming cuts off after the first chunk

The platform's reverse proxy (Traefik) has a 60s idle timeout by default. Long streams (Senate committees with 5 members) may exceed this. Raise the timeout in `traefik_dynamic.yml` (`transport.respondingTimeouts.idleTimeout`).

## Limitations

- **Senate is single-shot.** It does not iteratively refine across rounds. Each call is one propose → review → chair cycle.
- **Streaming is solo-only.** `ai/chat/stream/` is not available in Senate mode (it would require streaming N proposals and N reviews plus a chair).
- **Jules cannot self-merge.** The PR is opened but the user (or an admin) must review and merge. `JULES_AUTO_DEPLOY_PR` will deploy the branch after the PR is opened, but it will not auto-merge.
- **Code-review mode is 2-agent only.** When 3+ providers are configured, the platform uses the full Senate, not code review.
- **No fine-tuning.** The platform uses each provider's hosted model as-is. There is no support for custom LoRA adapters or fine-tuned models.
- **Encrypted keys are decryptable.** Anyone with the `BACKUP_ENCRYPTION_KEY` can read the saved provider keys. The encryption is at-rest against database compromise, not against admin compromise.
- **No multi-language system prompts.** The platform's system prompt is English. There is no per-locale or per-user system prompt override.
