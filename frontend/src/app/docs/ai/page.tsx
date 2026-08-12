'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Brain, BookOpen, ListChecks, Shield, Code2, Copy, Check, Wrench, Bot, Cpu, KeyRound, MessageSquare } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'providers', label: 'Providers', icon: Cpu },
    { id: 'configuration', label: 'Configuration', icon: KeyRound },
    { id: 'rate-limits', label: 'Rate Limits', icon: ListChecks },
    { id: 'senate', label: 'Senate Committee', icon: Brain },
    { id: 'jules', label: 'Jules Auto-Fix', icon: Bot },
    { id: 'prompt-injection', label: 'Prompt Injection', icon: Shield },
    { id: 'api-reference', label: 'API Reference', icon: Code2 },
    { id: 'security', label: 'Security', icon: Shield },
    { id: 'troubleshooting', label: 'Troubleshooting', icon: Wrench },
];

function CodeBlock({ children, lang = 'bash' }: { children: string; lang?: string }) {
    const [copied, setCopied] = useState(false);
    const copy = () => {
        navigator.clipboard.writeText(children.trim());
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };
    return (
        <div className="relative group my-4">
            <pre className="bg-slate-900 dark:bg-slate-900/80 text-slate-100 p-4 pr-12 rounded-xl overflow-x-auto text-sm leading-relaxed font-mono border border-slate-800 dark:border-slate-700/50">
                <code>{children.trim()}</code>
            </pre>
            <button
                onClick={copy}
                className="absolute top-3 right-3 p-1.5 rounded-lg bg-slate-700/60 hover:bg-slate-600 text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity"
                aria-label="Copy to clipboard"
            >
                {copied ? <Check size={14} /> : <Copy size={14} />}
            </button>
        </div>
    );
}

export default function AiDocsPage() {
    const [activeSection, setActiveSection] = useState('');

    useEffect(() => {
        const observer = new IntersectionObserver(
            (entries) => {
                const visible = entries.find(e => e.isIntersecting);
                if (visible) setActiveSection(visible.target.id);
            },
            { rootMargin: '-80px 0px -70% 0px' }
        );
        tocItems.forEach(item => {
            const el = document.getElementById(item.id);
            if (el) observer.observe(el);
        });
        return () => observer.disconnect();
    }, []);

    return (
        <main className="min-h-screen bg-white dark:bg-slate-950">

            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-purple-50/60 to-white dark:from-purple-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-purple-600 dark:text-purple-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-purple-100 dark:bg-purple-900/50 rounded-xl">
                            <Brain className="w-5 h-5 text-purple-700 dark:text-purple-300" />
                        </div>
                        <span className="text-sm font-semibold text-purple-600 dark:text-purple-400 uppercase tracking-wider">AI & Intelligence</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        AI & Intelligence
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        17 model providers, multi-agent Senate Committee, Jules auto-fix, and a runtime intelligence layer. Opt-in: nothing is enabled until an admin saves a key.
                    </p>
                </div>
            </section>

            <div className="max-w-7xl mx-auto flex gap-8 px-4 py-12">

                <aside className="hidden lg:block w-56 flex-shrink-0">
                    <nav className="sticky top-24 space-y-0.5">
                        <p className="text-xs font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-3 px-3">On this page</p>
                        {tocItems.map(item => {
                            const Icon = item.icon;
                            return (
                                <a
                                    key={item.id}
                                    href={`#${item.id}`}
                                    className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all ${
                                        activeSection === item.id
                                            ? 'bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-semibold'
                                            : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-slate-900'
                                    }`}
                                >
                                    <Icon size={14} className="flex-shrink-0" />
                                    <span className="truncate">{item.label}</span>
                                </a>
                            );
                        })}
                    </nav>
                </aside>

                <article className="flex-1 max-w-3xl prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">

                    <h2 id="overview" className="text-2xl font-bold flex items-center gap-2 mt-0">
                        <BookOpen className="w-5 h-5 text-purple-600" /> Overview
                    </h2>
                    <p>The AI subsystem has three layers:</p>
                    <ol>
                        <li><strong>Providers</strong> — A uniform interface over 17 third-party LLM APIs. The platform auto-discovers which providers are configured; a single key means &quot;solo&quot; mode, two-or-more keys means &quot;Senate Committee&quot; mode.</li>
                        <li><strong>Intelligence</strong> — Periodic background tasks that scan services, summarize deployments, and emit remediation recommendations. See <Link href="/docs/intelligence" className="text-purple-600 dark:text-purple-400 hover:underline">Intelligence (Runtime)</Link>.</li>
                        <li><strong>Jules auto-fix</strong> — A specialized agent that opens Pull Requests on failed deployments. Opt-in via <code>JULES_AUTO_DEPLOY_PR</code>.</li>
                    </ol>

                    <h2 id="providers" className="text-2xl font-bold flex items-center gap-2">
                        <Cpu className="w-5 h-5 text-purple-600" /> Supported Providers
                    </h2>
                    <p>The platform ships with adapters for 17 model providers. Each has a dedicated <code>*_API_KEY</code> env var and a default model.</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Provider</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Default model</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3">OpenAI</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">gpt-4o-mini</td></tr>
                                <tr><td className="p-3">Grok (xAI)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">grok-3-mini</td></tr>
                                <tr><td className="p-3">Gemini</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">gemini-2.0-flash</td></tr>
                                <tr><td className="p-3">Claude (Anthropic)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">claude-sonnet-4-20250514</td></tr>
                                <tr><td className="p-3">DeepSeek</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">deepseek-coder</td></tr>
                                <tr><td className="p-3">OpenRouter</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">openrouter/auto</td></tr>
                                <tr><td className="p-3">Groq</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">llama-3.3-70b-versatile</td></tr>
                                <tr><td className="p-3">Alibaba (Qwen)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">qwen-max</td></tr>
                                <tr><td className="p-3">Jules (Google)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">jules-latest</td></tr>
                                <tr><td className="p-3">Local LLM (Ollama / vLLM / LM Studio)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">local-model</td></tr>
                                <tr><td className="p-3">TruLay Cloud</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">smsly-latest</td></tr>
                                <tr><td className="p-3">FreeModel.dev</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">gpt-4o-mini</td></tr>
                                <tr><td className="p-3">OpenCode API</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">opencode-latest</td></tr>
                                <tr><td className="p-3">Mistral (La Plateforme)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">mistral-small-latest</td></tr>
                                <tr><td className="p-3">NVIDIA NIM</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">nvidia/llama-3.1-nemotron-70b-instruct</td></tr>
                                <tr><td className="p-3">Cloudflare Workers AI</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">@cf/meta/llama-3.1-8b-instruct</td></tr>
                                <tr><td className="p-3">Mock (fallback)</td><td className="p-3 font-mono text-purple-600 dark:text-purple-400">canned responses</td></tr>
                            </tbody>
                        </table>
                    </div>

                    <h2 id="configuration" className="text-2xl font-bold flex items-center gap-2">
                        <KeyRound className="w-5 h-5 text-purple-600" /> Provider Configuration
                    </h2>
                    <p>Provider configuration is a singleton row in <code>AIProviderSettings</code> (pk=1). It is created automatically on first access via <code>AIProviderSettings.get_solo()</code>. API keys are <code>EncryptedCharField</code> (Fernet) and are never returned in API responses — only the configured / unconfigured status is exposed.</p>
                    <p>There are two ways to configure a provider:</p>
                    <ol>
                        <li><strong>UI</strong> — Settings → AI → Providers. Save keys per provider. The UI never displays the saved key (only a &quot;configured&quot; badge).</li>
                        <li><strong>API</strong> — <code>POST /api/v1/ai/providers/update/</code> (admin only). The body is a partial update of the singleton.</li>
                    </ol>
                    <p>Either path calls <code>_sync_db_to_env()</code> which writes the keys into the worker process&apos;s environment so the next LLM call picks them up.</p>
                    <h3>The <code>_validate_https_allowlist</code> Gate</h3>
                    <p>The Jules provider&apos;s <code>jules_base_url</code> is validated against <code>settings.JULES_ALLOWED_HOSTS</code> (default <code>[&apos;api.jules.google.com&apos;]</code>). Any other host is rejected at <code>clean()</code> time. The validator requires <code>https://</code> and the host to be in the allowlist. This prevents an admin from accidentally pointing Jules at an attacker-controlled endpoint.</p>

                    <h2 id="rate-limits" className="text-2xl font-bold flex items-center gap-2">
                        <ListChecks className="w-5 h-5 text-purple-600" /> Rate Limits
                    </h2>
                    <p>The AI endpoints are throttled to prevent accidental cost overruns:</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Throttle</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Rate</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Scope</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono font-medium text-purple-600 dark:text-purple-400">AIChatRateThrottle</td><td className="p-3">30/minute</td><td className="p-3">per user — chat endpoints</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-purple-600 dark:text-purple-400">AIAnalysisRateThrottle</td><td className="p-3">10/minute</td><td className="p-3">per user — analysis endpoints</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-purple-600 dark:text-purple-400">UserAICap</td><td className="p-3">daily cap</td><td className="p-3">per user — all endpoints</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>The <code>UserAICap</code> model holds the per-user daily cap. It defaults to:</p>
                    <ul>
                        <li><code>daily_token_cap = 100000</code> (tokens/day)</li>
                        <li><code>daily_cost_cap_usd = 10.00</code> (USD/day)</li>
                    </ul>
                    <p>When the cap is exceeded the API returns HTTP 429 with a reason (<code>Daily token cap exceeded</code> or <code>Daily cost cap exceeded</code>). The Senate Committee applies a 3× multiplier on the cap pre-flight check (<code>SENATE_COMMITTEE_COST_MULTIPLIER</code>).</p>

                    <h2 id="senate" className="text-2xl font-bold flex items-center gap-2">
                        <Brain className="w-5 h-5 text-purple-600" /> Senate Committee
                    </h2>
                    <p>When two or more providers are configured and <code>senate_enabled=True</code>, the chat endpoints switch from solo mode to a three-phase deliberation. The committee is capped at <code>senate_max_members</code> (default 5) — only the first N configured providers participate.</p>

                    <h3>Phase 1 — Propose</h3>
                    <p>Every committee member answers the prompt independently and in parallel. Each call has a <code>SENATE_TIMEOUT_SECONDS</code> timeout (default 180s). The parallel pool uses <code>ThreadPoolExecutor(max_workers=len(providers))</code> with <code>cancel_futures=True</code> on timeout.</p>

                    <h3>Phase 2 — Review</h3>
                    <p>Each provider receives <strong>all</strong> other proposals and is asked to review and vote. Voting is a structured &quot;I agree with member X because …&quot; or &quot;I disagree with member X because …&quot;. This phase is also parallelized.</p>

                    <h3>Phase 3 — Chair</h3>
                    <p>A chair (rotated: the second configured provider by default, falling back to the first) receives all proposals and reviews, then synthesizes a final resolution. If the chair fails (timeout, 5xx, bad JSON), the next configured provider in the list is rotated in as chair and the phase is retried.</p>
                    <p>The user-facing response is the chair&apos;s resolution. The audit log records the full deliberation as <code>metadata.votes</code> and <code>metadata.resolution</code>.</p>

                    <h3>Code Review Mode</h3>
                    <p>When exactly two providers are configured and <code>mode=auto</code> (the default), the platform uses a lighter 2-agent code-review instead of the full Senate. The two agents cross-review each other (4 API calls total) and the user receives both reviews. This is cheaper than the Senate and produces results in ~half the time.</p>

                    <h2 id="jules" className="text-2xl font-bold flex items-center gap-2">
                        <Bot className="w-5 h-5 text-purple-600" /> Jules Auto-Fix
                    </h2>
                    <p>Jules is a specialized agent for fixing failed deployments. It is opt-in and gated by <code>JULES_AUTO_DEPLOY_PR</code>. The flow:</p>
                    <ol>
                        <li>A deployment fails (<code>status=FAILED</code>).</li>
                        <li><code>_collect_failure_context()</code> builds a prompt: deployment ID, last 10000 chars of build logs, plus monitoring context (CPU / memory / OOM events / crash-loop detection) from the <code>ScalingAnalyzer</code>.</li>
                        <li>The prompt is sent to Jules and Jules returns a structured JSON: <code>&#123;fix_description, files_to_change, suggested_changes&#125;</code>.</li>
                        <li>The agent clones the repository, creates a branch <code>jules/auto-fix-&lt;deployment-id&gt;</code>, applies the suggested changes, commits, and pushes.</li>
                        <li>A Pull Request is opened on GitHub (or GitLab / Bitbucket).</li>
                        <li>If <code>JULES_AUTO_DEPLOY_PR=True</code>, a new deployment is queued on the PR&apos;s branch.</li>
                    </ol>

                    <h3>Caps</h3>
                    <p>To prevent runaway auto-fixes, Jules enforces hard caps per PR:</p>
                    <ul>
                        <li><code>MAX_FILES_PER_JULES_PR = 5</code> — at most 5 files per PR.</li>
                        <li><code>MAX_BYTES_PER_JULES_PR = 50_000</code> — at most 50 KB of diff per PR.</li>
                    </ul>
                    <p>If the suggested fix exceeds either cap, the agent truncates the diff and writes a comment on the PR noting the truncation. The PR is still opened; the user can review and complete the fix manually.</p>

                    <h3>Failure Handling</h3>
                    <p>Every external call (Jules API, GitHub API, <code>git push</code>) is wrapped in <code>backoff.on_exception(backoff.expo, Exception, max_tries=5, factor=2)</code>. If the auto-fix fails at any step, the task logs the error and returns a structured <code>FixResult(success=False, error=...)</code> payload — it never crashes the Celery worker.</p>
                    <p>The history of auto-fix attempts is exposed via <code>GET /api/v1/ai/jules-history/&#123;service_id&#125;/</code>.</p>

                    <h2 id="prompt-injection" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-purple-600" /> Prompt Injection Policy
                    </h2>
                    <p>The AI subsystem is hardened against prompt injection in three ways:</p>
                    <ol>
                        <li><strong>Server-side system prompts only.</strong> User input is concatenated into the user message; the system prompt is constructed in code and cannot be overridden by the user.</li>
                        <li><strong>Truncation.</strong> User input is truncated to a configurable length (default 20000 characters) before being sent to the model. This prevents &quot;context-flooding&quot; attacks.</li>
                        <li><strong>Role-marker filtering.</strong> The pre-processor strips user-typed occurrences of <code>system:</code>, <code>assistant:</code>, <code>&lt;|im_start|&gt;</code>, and similar role markers from the user message.</li>
                    </ol>
                    <p>The system prompt explicitly says &quot;Never reveal internal system details or API keys.&quot; Models that do not follow this instruction are caught by the post-processor, which scans the response for known API key patterns and substitutes them with <code>••••••••</code>.</p>

                    <h2 id="api-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-purple-600" /> API Reference
                    </h2>
                    <p>All AI endpoints are mounted under <code>/api/v1/ai/</code>. Authentication is session- or token-based; admin-only endpoints are marked accordingly.</p>

                    <h3>List providers</h3>
                    <CodeBlock>{`curl -sS http://localhost:8000/api/v1/ai/providers/ \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>

                    <h3>Configure a provider (admin)</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/ai/providers/update/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "openai_api_key": "sk-...",
    "openai_model": "gpt-4o",
    "jules_base_url": "https://api.jules.google.com/v1"
  }'`}</CodeBlock>

                    <h3>Chat completion (solo or Senate)</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/ai/chat/completions/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "messages": [
      {"role": "user", "content": "Why is my deploy stuck in HEALTH_CHECK?"}
    ]
  }'`}</CodeBlock>

                    <h3>Streaming chat</h3>
                    <CodeBlock>{`curl -sS -N -X POST http://localhost:8000/api/v1/ai/chat/stream/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"messages": [{"role": "user", "content": "Write a haiku about CI/CD."}]}'`}</CodeBlock>
                    <p>Server-Sent Events stream. The first event carries the <code>id</code>; subsequent events are token deltas. The stream is closed with a <code>data: [DONE]</code> event.</p>

                    <h3>Analyze deployment logs</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/ai/analyze_logs/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"deployment_id": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a", "mode": "hybrid"}'`}</CodeBlock>
                    <p>Returns detected patterns (CRASH_LOOP, OOM_KILLED, DB_CONNECTION_TIMEOUT, etc.) and a confidence score per pattern.</p>

                    <h3>Jules history for a service</h3>
                    <CodeBlock>{`curl -sS http://localhost:8000/api/v1/ai/jules-history/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/ \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>

                    <div className="not-prose mt-4 p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-start gap-3">
                        <Code2 className="w-5 h-5 text-purple-600 dark:text-purple-400 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">Full API reference</p>
                            <p className="text-sm text-slate-600 dark:text-slate-400">
                                See <Link href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/docs/ai.md" className="text-purple-600 dark:text-purple-400 hover:underline font-medium">docs/ai.md</Link> for every endpoint, request body, response field, and error code — including <code>ai/test/</code>, <code>ai/cost-estimate/</code>, and the <code>include_balance=true</code> query parameter.
                            </p>
                        </div>
                    </div>

                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-purple-600" /> Security
                    </h2>

                    <h3>Encrypted API Keys at Rest</h3>
                    <p>All 17 provider API keys are <code>EncryptedCharField(max_length=500)</code> columns in <code>AIProviderSettings</code>. The encryption is Fernet (symmetric, AES-128-CBC + HMAC-SHA256) using the platform&apos;s <code>BACKUP_ENCRYPTION_KEY</code> (or a separate <code>AI_ENCRYPTION_KEY</code> if set) as the master key.</p>
                    <p>Key rotation: re-encrypt the rows with the new key, then update <code>.env</code> and restart. There is no in-place re-key tool; the recommended path is to re-save each key through the UI after the restart.</p>

                    <h3>Per-User Spend Caps</h3>
                    <p><code>UserAICap</code> is a one-to-one table on <code>User</code>. Defaults are <code>daily_token_cap=100000</code> and <code>daily_cost_cap_usd=10.00</code>. To raise the cap for a specific user, edit the row directly or call <code>UserAICap.objects.update_or_create(user=…, defaults=&#123;…&#125;)</code> in a Django shell.</p>
                    <p>The cap is recomputed on every LLM call. It is not a per-second or per-minute cap — the only per-second throttling is the DRF throttle classes.</p>

                    <h3>Senate Committee Cost Multiplier</h3>
                    <p>The Senate Committee pre-flight divides the user&apos;s cap by 3 (<code>SENATE_COMMITTEE_COST_MULTIPLIER</code>). This is a conservative guard: a typical Senate call uses 3× the tokens of a solo call (one propose + one review + one chair), so the pre-divided cap roughly matches the post-call usage.</p>

                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-purple-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;No AI providers configured. Add an API key in Settings &gt; AI.&quot;</h3>
                    <p>None of the 17 provider keys are set. Open Settings → AI → Providers and save at least one. The platform will not auto-fall-back to mock mode in production.</p>

                    <h3>&quot;Provider X failed: 401 Unauthorized&quot;</h3>
                    <p>The API key is invalid or has been rotated. Re-save the key in Settings → AI. The platform&apos;s <code>_sync_db_to_env()</code> runs on every save and writes the new key into the worker environment.</p>

                    <h3>&quot;Provider X failed: 429 Too Many Requests&quot;</h3>
                    <p>The provider is rate-limiting the platform. The default retry is 3 attempts with exponential back-off (<code>retry_429(max_retries=3, base_delay=2.0)</code>). After 3 failures the call is recorded in <code>LLMUsage</code> with zero tokens and the user gets a 502.</p>

                    <h3>&quot;Daily cost cap exceeded&quot;</h3>
                    <p>The user has hit their <code>UserAICap.daily_cost_cap_usd</code>. Either wait until tomorrow or raise the cap in a Django shell.</p>

                    <h3>&quot;Jules auto-fix did not create a PR&quot;</h3>
                    <p>Inspect <code>GET /api/v1/ai/jules-history/&#123;service_id&#125;/</code> for the failure reason. The most common cause is that the suggested fix exceeded <code>MAX_FILES_PER_JULES_PR</code> (5) or <code>MAX_BYTES_PER_JULES_PR</code> (50000).</p>

                    <h3>&quot;Provider &apos;jules&apos; not in JULES_ALLOWED_HOSTS&quot;</h3>
                    <p>The platform&apos;s <code>JULES_ALLOWED_HOSTS</code> setting is missing the host portion of <code>jules_base_url</code>. Default is <code>[&apos;api.jules.google.com&apos;]</code>. If you self-host Jules, add the host to the allowlist in <code>.env</code>:</p>
                    <CodeBlock>{`JULES_ALLOWED_HOSTS=api.jules.google.com,jules.internal.example.com`}</CodeBlock>

                    <h3>Streaming cuts off after the first chunk</h3>
                    <p>The platform&apos;s reverse proxy (Traefik) has a 60s idle timeout by default. Long streams (Senate committees with 5 members) may exceed this. Raise the timeout in <code>traefik_dynamic.yml</code> (<code>transport.respondingTimeouts.idleTimeout</code>).</p>

                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs/deployments" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> Deployments
                        </Link>
                        <Link href="/docs/intelligence" className="flex items-center gap-1.5 text-sm text-purple-600 dark:text-purple-400 hover:underline font-medium">
                            Intelligence (Runtime) <ArrowRight size={14} />
                        </Link>
                    </div>

                </article>
            </div>
        </main>
    );
}
