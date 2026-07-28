'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Zap, BookOpen, Cpu, Brain, Shield, Code2, Copy, Check, Wrench, ListChecks, Bot, Lock } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'classic-engine', label: 'Classic Engine', icon: Cpu },
    { id: 'ai-enhanced', label: 'AI-Enhanced Engine', icon: Brain },
    { id: 'admin-surface', label: 'Admin Surface', icon: ListChecks },
    { id: 'security', label: 'Security', icon: Shield },
    { id: 'api-reference', label: 'API Reference', icon: Code2 },
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

export default function AutoscalingDocsPage() {
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

            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-emerald-100 dark:bg-emerald-900/50 rounded-xl">
                            <Zap className="w-5 h-5 text-emerald-700 dark:text-emerald-300" />
                        </div>
                        <span className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Autoscaling</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Autoscaling
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        Three engines, one shared state. CPU-based hysteresis, Prometheus + Loki + AI for capacity, and a K8s-style admin surface for manual control.
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
                                            ? 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 font-semibold'
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
                        <BookOpen className="w-5 h-5 text-emerald-600" /> Overview
                    </h2>
                    <p>Grid ships three autoscaler implementations that work together. The classic CPU-based engine handles day-to-day scale up / scale down with predictable hysteresis. The AI-enhanced engine adds Prometheus + Loki metrics, anomaly detection, and a paginated batch driver. The K8s / Docker admin surface provides manual replica control for operators.</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Path</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Module</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Trigger</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Scope</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium text-emerald-600 dark:text-emerald-400">Classic CPU</td>
                                    <td className="p-3 font-mono">services/autoscaler.py</td>
                                    <td className="p-3">Celery beat, every minute</td>
                                    <td className="p-3">Every service, CPU threshold</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-emerald-600 dark:text-emerald-400">AI-enhanced</td>
                                    <td className="p-3 font-mono">tasks_autoscale.py + scaling_ai.py</td>
                                    <td className="p-3">Celery beat, every 60s</td>
                                    <td className="p-3">Every service, Prom + Loki + AI</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-emerald-600 dark:text-emerald-400">K8s / Docker admin</td>
                                    <td className="p-3 font-mono">apps/autoscaler/views.py</td>
                                    <td className="p-3">Manual (HTTP)</td>
                                    <td className="p-3">One service, per-call</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <p>The classic engine is the <strong>default</strong> and is what the platform runs out of the box. The AI-enhanced engine is opt-in via <code>AUTOSCALER_AI_ENABLED=True</code> and requires the <code>prometheus_loki</code> integration. The admin surface is always available but requires <code>IsAdminUser</code>.</p>
                    <p>All three share the same <code>Service</code> fields (<code>min_replicas</code>, <code>max_replicas</code>, <code>autoscale_cpu_target</code>, <code>last_scale_at</code>) and the same <code>MAX_REPLICAS</code> global guard. They coordinate via a single row-level lock (see <a href="#security" className="text-emerald-600 dark:text-emerald-400 hover:underline">Race Conditions</a>).</p>

                    <h2 id="classic-engine" className="text-2xl font-bold flex items-center gap-2">
                        <Cpu className="w-5 h-5 text-emerald-600" /> Classic Engine
                    </h2>
                    <p>The classic engine is a CPU-based, two-threshold controller with asymmetric cooldowns. It runs on Celery beat once per minute.</p>

                    <h3>How It Works</h3>
                    <p>For each service with <code>min_replicas &gt; 0</code> (or <code>autoscale_cpu_target &gt; 0</code>):</p>
                    <ol>
                        <li>Read the <strong>current</strong> CPU average over the last minute (sourced from <code>docker stats</code> on the local node, or from a <code>ManagedServer</code> proxy call on a remote node).</li>
                        <li>Compare to <code>autoscale_cpu_target</code> (default 70).</li>
                        <li><strong>Scale up</strong> if <code>cpu &gt; target + 5%</code> (hysteresis) AND the service is not in cooldown.</li>
                        <li><strong>Scale down</strong> if <code>cpu &lt; target - 20%</code> (wider hysteresis on the way down) AND the service is not in cooldown.</li>
                        <li>Update <code>Service.last_scale_at</code> and exit.</li>
                    </ol>
                    <p>The asymmetric cooldown is the key invariant: <strong>scale-up cooldown is 1 minute, scale-down cooldown is 5 minutes</strong>. This is hard-coded and not configurable per service.</p>

                    <h3>The <code>last_scale_at</code> Field (NOT <code>updated_at</code>)</h3>
                    <p>The cooldown is computed from <code>Service.last_scale_at</code>, <strong>not</strong> from <code>Service.updated_at</code>. The <code>updated_at</code> field is touched by any model save (env var edit, manual replica change, settings update) — using it for cooldown would let a side effect reset the autoscaler&apos;s clock. The <code>last_scale_at</code> field is only written by the autoscaler itself, on a real scale event. The same field is also written by the AI-enhanced engine so the two engines cannot oscillate against each other on the same service.</p>

                    <h2 id="ai-enhanced" className="text-2xl font-bold flex items-center gap-2">
                        <Brain className="w-5 h-5 text-emerald-600" /> AI-Enhanced Engine
                    </h2>
                    <p>The AI-enhanced engine is a superset of the classic one. It uses Prometheus for CPU / memory metrics, Loki for runtime log volume, and (when configured) the Senate Committee for capacity recommendations. It runs on a 60-second beat.</p>

                    <h3>Prometheus + Loki Integration</h3>
                    <p>Metrics are scraped from the platform&apos;s Prometheus instance. The engine queries:</p>
                    <ul>
                        <li><code>sum(rate(container_cpu_usage_seconds_total&#123;service=~&quot;&lt;name&gt;&quot;&#125;[1m]))</code> — CPU rate</li>
                        <li><code>sum(container_memory_usage_bytes&#123;service=~&quot;&lt;name&gt;&quot;&#125;)</code> — memory footprint</li>
                        <li><code>sum(rate(loki_log_entries_total&#123;service=~&quot;&lt;name&gt;&quot;&#125;[1m]))</code> — log volume rate</li>
                    </ul>
                    <p>If the platform&apos;s Loki is not running, the engine falls back to the classic <code>docker stats</code> path. The integration is detected at runtime via the <code>PROMETHEUS_LIVE</code> and <code>LOKI_LIVE</code> flags on <code>PlatformConfig</code>.</p>

                    <h3>Paginated Batch via <code>id__gt</code> Cursor</h3>
                    <p>The engine walks all services in batches of 100 using a keyset cursor on the primary key:</p>
                    <CodeBlock lang="python">{`qs = Service.objects.filter(id__gt=cursor).order_by("id")[:100]`}</CodeBlock>
                    <p>This avoids the <code>OFFSET</code> performance cliff on large fleets. The cursor is held in <code>cache.set(&quot;autoscale:cursor&quot;, last_id, 600)</code> so a worker crash resumes from the same point. The walk is incremental: each 60-second tick advances the cursor by 100 services. A fleet of 10 000 services takes 100 ticks (~100 minutes) to complete a full sweep. The cursor is reset to 0 at the end of a sweep.</p>

                    <h3>AI Recommendations</h3>
                    <p>When <code>AUTOSCALER_AI_ENABLED=True</code> and an LLM is configured, the engine consults the Senate Committee on scale-up decisions that exceed <code>max_replicas * 0.8</code> (i.e. the engine is about to hit the ceiling). The model is asked: &quot;given the last 24 hours of CPU, memory, and request volume, should we raise <code>max_replicas</code> or hold it?&quot; The response is logged to <code>AuditLog</code> with <code>actor=&apos;AI_SCALER&apos;</code> and is <strong>advisory only</strong> — the engine does not auto-raise <code>max_replicas</code> based on the model output. An operator must approve the change in the UI or via API.</p>

                    <h2 id="admin-surface" className="text-2xl font-bold flex items-center gap-2">
                        <ListChecks className="w-5 h-5 text-emerald-600" /> K8s / Docker Admin
                    </h2>
                    <p>The admin surface exposes a manual replica controller. It requires <code>IsAdminUser</code> (staff status) and is gated by <code>ADMIN_AUTOSCALER_ENABLED</code> (env, default <code>True</code>).</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Endpoint</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Method</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Purpose</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono">/api/v1/scaling/analyze/</td><td className="p-3">POST</td><td className="p-3">One-shot analysis (current state + recommendation).</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/scaling/spawn/</td><td className="p-3">POST</td><td className="p-3">Force-spawn a replica. Bypasses cooldowns.</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/scaling/replicas/</td><td className="p-3">GET</td><td className="p-3">List current replica state.</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/scaling/destroy_replica/</td><td className="p-3">POST</td><td className="p-3">Force-destroy a specific replica.</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/scaling/alert_config/</td><td className="p-3">PUT</td><td className="p-3">Update <code>Service.alert_config</code>.</td></tr>
                            </tbody>
                        </table>
                    </div>

                    <h3>Alert Config</h3>
                    <p><code>Service.alert_config</code> is a JSONField added in Batch C. It holds the per-service alert thresholds and the channel list. The schema is:</p>
                    <CodeBlock lang="json">{`{
  "cpu_threshold": 85,
  "memory_threshold": 90,
  "error_rate_threshold": 0.05,
  "channels": ["email", "slack"],
  "slack_webhook_url": "https://hooks.slack.com/...",
  "cooldown_minutes": 15
}`}</CodeBlock>
                    <p><code>PUT /api/v1/scaling/alert_config/</code> accepts a partial body. The <code>slack_webhook_url</code> is <code>EncryptedCharField</code> on a related row (not in the JSON) and is never echoed back in responses.</p>
                    <p>When the engine observes a breach, it writes an <code>AuditLog</code> row and emits the configured channels. The <code>cooldown_minutes</code> field prevents the same alert from firing more than once per window per channel.</p>

                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-emerald-600" /> Security
                    </h2>

                    <h3>MAX_REPLICAS Guard</h3>
                    <p>A global <code>MAX_REPLICAS</code> env var (default 32) caps the replica count on a single service. The classic engine, the AI-enhanced engine, and the admin surface all respect this cap. The check is enforced <strong>before</strong> the spawn — a request to set <code>desired_replicas=64</code> is rejected with HTTP 400, not silently capped.</p>

                    <h3>Race Conditions (Now Fixed)</h3>
                    <p>A long-standing bug was that two concurrent scale events (e.g. a manual <code>spawn/</code> and the AI-enhanced engine&apos;s tick) could both observe <code>current_replicas=2</code>, both decide to add one, and end up with <code>replicas=4</code> instead of the intended <code>3</code>.</p>
                    <p>The fix: every scale event acquires a <code>SELECT … FOR UPDATE</code> row lock on the <code>Service</code> row for the duration of the read-decide-write cycle. The lock is held inside a <code>transaction.atomic()</code> block. The classic engine and the AI-enhanced engine both use the same pattern; the admin surface uses it too. Concurrent calls serialize on the lock and only one observes the up-to-date <code>current_replicas</code>.</p>
                    <p>A residual race that <strong>cannot</strong> be fixed at the row level: a <code>min_replicas</code> change and a deploy starting at the same time. The deploy&apos;s <code>queued_min_replicas</code> snapshot (see <Link href="/docs/deployments" className="text-emerald-600 dark:text-emerald-400 hover:underline">Deployments</Link>) covers this case — the deploy uses the snapshot, not the live field.</p>

                    <h3>Audit Log</h3>
                    <p>Every scale event writes an <code>AuditLog</code> row with:</p>
                    <ul>
                        <li><code>actor</code> — the engine or admin user that triggered the event.</li>
                        <li><code>action</code> — <code>SCALE_UP</code>, <code>SCALE_DOWN</code>, <code>SPAWN</code>, <code>DESTROY_REPLICA</code>, <code>ALERT_FIRED</code>.</li>
                        <li><code>target</code> — the service name.</li>
                        <li><code>metadata</code> — old / new replica count, the reason, and (for the AI engine) the model output that drove the decision.</li>
                    </ul>
                    <p>The audit log is hash-linked — see <code>models_audit.py</code>. Manual <code>spawn/</code> and <code>destroy_replica/</code> calls log the calling admin&apos;s user ID.</p>

                    <h2 id="api-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-emerald-600" /> API Reference
                    </h2>
                    <p>All endpoints are mounted under <code>/api/v1/scaling/</code>. Admin endpoints require <code>IsAdminUser</code>. Service-level reads require the service owner.</p>

                    <h3>Analyze a service</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/scaling/analyze/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21"}'`}</CodeBlock>
                    <p>Non-mutating. Returns the current state and a recommended <code>desired_replicas</code>.</p>

                    <h3>Force-spawn a replica (admin)</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/scaling/spawn/ \\
  -H "Authorization: Token $SMSLY_ADMIN_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21", "count": 1}'`}</CodeBlock>
                    <p>Bypasses cooldowns. Capped at <code>MAX_REPLICAS</code> and <code>Service.max_replicas</code>.</p>

                    <h3>List current replicas</h3>
                    <CodeBlock>{`curl -sS "http://localhost:8000/api/v1/scaling/replicas/?service_id=9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21" \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>

                    <h3>Destroy a specific replica (admin)</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/scaling/destroy_replica/ \\
  -H "Authorization: Token $SMSLY_ADMIN_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21", "container_id": "abc123"}'`}</CodeBlock>
                    <p>Refuses to destroy the last replica if <code>min_replicas &gt;= 1</code>.</p>

                    <h3>Update alert config</h3>
                    <CodeBlock>{`curl -sS -X PUT http://localhost:8000/api/v1/scaling/alert_config/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "cpu_threshold": 75,
    "channels": ["email", "slack"],
    "slack_webhook_url": "https://hooks.slack.com/services/..."
  }'`}</CodeBlock>
                    <p>The service owner (not just admins) can call this. <code>slack_webhook_url</code> is encrypted at rest and never echoed back.</p>

                    <div className="not-prose mt-4 p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-start gap-3">
                        <Code2 className="w-5 h-5 text-emerald-600 dark:text-emerald-400 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">Full API reference</p>
                            <p className="text-sm text-slate-600 dark:text-slate-400">
                                See <Link href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/docs/autoscaling.md" className="text-emerald-600 dark:text-emerald-400 hover:underline font-medium">docs/autoscaling.md</Link> in the repository for the full alert_config schema, error codes, and the <code>MAX_REPLICAS</code> guard&apos;s behavior on edge cases.
                            </p>
                        </div>
                    </div>

                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-emerald-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;Service is at min_replicas but CPU is 100%&quot;</h3>
                    <p>Either the CPU is a transient spike and the cooldown will trigger a scale-up, or the engine is throttled. The classic engine scales up at 1-minute intervals; if CPU is at 100% for a full minute, the next tick will scale it up to <code>min_replicas + 1</code>. To force an immediate scale-up, use the <code>spawn/</code> endpoint.</p>

                    <h3>&quot;AI-enhanced engine is not running&quot;</h3>
                    <p>Check <code>AUTOSCALER_AI_ENABLED=True</code> in <code>.env</code>. Then check <code>PlatformConfig.prometheus_loki_live</code> — both Prometheus and Loki must be reachable. The engine logs a warning and falls back to the classic path if either is down.</p>

                    <h3>&quot;Replica count is stuck at MAX_REPLICAS&quot;</h3>
                    <p><code>MAX_REPLICAS</code> is a global cap. To raise it, edit <code>.env</code> and restart the backend. The new value is read at boot; there is no hot reload.</p>

                    <h3>&quot;Autoscaler is oscillating&quot;</h3>
                    <p>Check the cooldowns: 1 minute up, 5 minutes down. If your workload has high variance on the order of minutes, the asymmetric cooldown will still produce flapping. Lower <code>autoscale_cpu_target</code> so the engine is less aggressive, or set <code>min_replicas</code> to the average demand and let the engine only handle spikes.</p>

                    <h3>&quot;alert_config was reset to defaults after a deploy&quot;</h3>
                    <p>The default values are emitted on every service create, and the engine backfills defaults for older services when they are first scaled by the AI engine. To permanently override, save the values via <code>PUT /api/v1/scaling/alert_config/</code>.</p>

                    <h3>&quot;Manual destroy_replica fails with &apos;cannot destroy last replica&apos;&quot;</h3>
                    <p><code>Service.min_replicas &gt;= 1</code> and there is only one running replica. Set <code>min_replicas=0</code> first, then destroy the replica.</p>

                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs/functions" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> Functions
                        </Link>
                        <Link href="/docs/intelligence" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
                            Intelligence (Runtime) <ArrowRight size={14} />
                        </Link>
                    </div>

                </article>
            </div>
        </main>
    );
}
