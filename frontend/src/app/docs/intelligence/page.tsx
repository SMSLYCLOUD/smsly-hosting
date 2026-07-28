'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Activity, BookOpen, Code2, Copy, Check, Shield, Wrench, Brain, ListChecks, GitBranch } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'periodic-tasks', label: 'Periodic Tasks', icon: Activity },
    { id: 'codemap', label: 'AI Codemap', icon: GitBranch },
    { id: 'anomaly-detection', label: 'Anomaly Detection', icon: ListChecks },
    { id: 'self-healing', label: 'Self-Healing', icon: Brain },
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

export default function IntelligenceDocsPage() {
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

            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-cyan-50/60 to-white dark:from-cyan-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-cyan-600 dark:text-cyan-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-cyan-100 dark:bg-cyan-900/50 rounded-xl">
                            <Activity className="w-5 h-5 text-cyan-700 dark:text-cyan-300" />
                        </div>
                        <span className="text-sm font-semibold text-cyan-600 dark:text-cyan-400 uppercase tracking-wider">Intelligence (Runtime)</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Intelligence (Runtime)
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        The always-on watchdog. Periodic scans over every service, anomaly detection in build logs, and a remediation engine that proposes (or applies) fixes. No LLM required.
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
                                            ? 'bg-cyan-50 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-300 font-semibold'
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
                        <BookOpen className="w-5 h-5 text-cyan-600" /> Overview
                    </h2>
                    <p>The intelligence subsystem is a small set of Celery periodic tasks and a remediation engine. Unlike the chat / Senate subsystem, it does not require an LLM to be configured — the engine falls back to rule-only mode when no provider is available.</p>
                    <p>The boundary between the two layers is:</p>
                    <ul>
                        <li><strong>AI</strong> (see <Link href="/docs/ai" className="text-cyan-600 dark:text-cyan-400 hover:underline">AI &amp; Intelligence</Link>) — interactive, user-driven, LLM-billable. Throttled by <code>AIChatRateThrottle</code> and <code>AIAnalysisRateThrottle</code>. Always requires an authenticated user.</li>
                        <li><strong>Intelligence</strong> (this page) — automated, schedule-driven, runs in the background. Does not require user input and does not hit the per-user LLM cap.</li>
                    </ul>
                    <p>The two layers share the <code>apps.intelligence</code> Django app and the same <code>provider</code> configuration, but they are not coupled: you can disable the AI chat endpoints and still have the periodic scans run.</p>

                    <h2 id="periodic-tasks" className="text-2xl font-bold flex items-center gap-2">
                        <Activity className="w-5 h-5 text-cyan-600" /> Periodic Tasks
                    </h2>
                    <p>There are three Celery beat schedules that power the runtime layer. They are registered when the <code>apps.intelligence</code> app boots.</p>

                    <h3><code>detect_anomalies_task</code> — every 3 minutes</h3>
                    <p>Runs <code>LogAnalyzer.analyze_logs()</code> over the last 20000 chars of each service&apos;s latest deployment logs, plus a health-status fallback. Detected patterns (CRASH_LOOP, OOM_KILLED, DB_CONNECTION_TIMEOUT, etc.) are passed to the <code>RemediationEngine.apply_fix()</code> for auto-remediation.</p>
                    <p>The scan walks services in a paginated batch of 100. Each service is processed in its own <code>try</code> / <code>except</code> so a single broken service does not abort the entire scan. The summary is logged at INFO and returned to the Celery result backend:</p>
                    <CodeBlock lang="json">{`{
  "checked": 247,
  "issues_detected": 3,
  "auto_fixed": 2,
  "errors": 1
}`}</CodeBlock>

                    <h3><code>proactive_health_scan_task</code> — every 5 minutes</h3>
                    <p>Walks every service with <code>health_status=&apos;unhealthy&apos;</code> and calls <code>RemediationEngine.apply_fix(&apos;HEALTH_CHECK_FAIL&apos;, service_id)</code>. The remediation action is <code>RESTART_OR_ROLLBACK</code> — it first attempts a container restart, then rolls back to the previous <code>ACTIVE</code> deployment if the restart does not bring the service back to healthy.</p>
                    <p>This is intentionally conservative: it only operates on services that are <strong>already</strong> marked unhealthy. It does not speculatively restart healthy services.</p>

                    <h3><code>daily_intelligence_report_task</code> — 06:00 UTC</h3>
                    <p>Generates a daily summary of the last 24 hours. The report covers:</p>
                    <ul>
                        <li>Total deployments.</li>
                        <li>Failed deployments.</li>
                        <li>Success rate.</li>
                        <li>Number of anomalies detected (from <code>AuditLog</code> rows with <code>actor in [&apos;AI_REMEDIATOR&apos;, &apos;AI_REVIEWER&apos;]</code>).</li>
                    </ul>
                    <p>The report is stored as an <code>AuditLog</code> row with <code>actor=&apos;AI_REPORTER&apos;</code>, <code>action=&apos;DAILY_REPORT&apos;</code>, and <code>target=&apos;SYSTEM&apos;</code>. Reports are immutable and form a permanent, hash-chained daily ledger.</p>

                    <h2 id="codemap" className="text-2xl font-bold flex items-center gap-2">
                        <GitBranch className="w-5 h-5 text-cyan-600" /> AI Codemap
                    </h2>
                    <p>The <code>LogAnalyzer</code> class is the platform&apos;s primary log-pattern recognizer. It maintains a small library of regex / heuristic patterns and a confidence score per pattern. When the configured LLM is available, ambiguous patterns are sent to the model for confirmation; the response is folded into the confidence score.</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Issue</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Pattern</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Confidence (rule)</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">LLM-confirmed</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">OOM_KILLED</td><td className="p-3">exit 137 / <code>Out of memory</code></td><td className="p-3">0.95</td><td className="p-3">0.99</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">CRASH_LOOP</td><td className="p-3">restarted &gt;3× in 5min</td><td className="p-3">0.85</td><td className="p-3">0.92</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DB_CONNECTION_TIMEOUT</td><td className="p-3"><code>psycopg2.OperationalError</code></td><td className="p-3">0.80</td><td className="p-3">0.90</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">BUILD_FAILURE</td><td className="p-3"><code>npm ERR!</code>, <code>pip: command not found</code></td><td className="p-3">0.90</td><td className="p-3">0.95</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">HEALTH_CHECK_FAIL</td><td className="p-3"><code>GET /health</code> returning 5xx</td><td className="p-3">0.90</td><td className="p-3">0.95</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">SSL_CERT_EXPIRED</td><td className="p-3"><code>x509: certificate has expired</code></td><td className="p-3">0.95</td><td className="p-3">0.99</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DISK_FULL</td><td className="p-3"><code>No space left on device</code></td><td className="p-3">0.99</td><td className="p-3">0.99</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">PORT_CONFLICT</td><td className="p-3"><code>bind: address already in use</code></td><td className="p-3">0.95</td><td className="p-3">0.97</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DNS_FAILURE</td><td className="p-3"><code>no such host</code></td><td className="p-3">0.85</td><td className="p-3">0.92</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DEPENDENCY_MISSING</td><td className="p-3"><code>ModuleNotFoundError</code></td><td className="p-3">0.90</td><td className="p-3">0.95</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">TIMEOUT</td><td className="p-3"><code>context deadline exceeded</code></td><td className="p-3">0.70</td><td className="p-3">0.85</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>Patterns with confidence below 0.9 are surfaced to the dashboard but <strong>not</strong> auto-remediated — they wait for a human <code>approve</code> action via the AI assistant.</p>

                    <h2 id="anomaly-detection" className="text-2xl font-bold flex items-center gap-2">
                        <ListChecks className="w-5 h-5 text-cyan-600" /> Anomaly Detection
                    </h2>
                    <p>Anomalies are detected in two ways:</p>
                    <ol>
                        <li><strong>Pattern-based</strong> (see above) — the <code>LogAnalyzer</code> runs over the latest deployment&apos;s <code>build_logs</code> plus a health-status fallback (if a service is <code>health_status=&apos;unhealthy&apos;</code> for any reason and no log pattern matches, the analyzer synthesizes a <code>CRASH_LOOP</code> issue with confidence 0.9).</li>
                        <li><strong>AI-enhanced</strong> (when an LLM is configured) — ambiguous patterns are sent to the model with the last 20000 chars of logs. The model&apos;s response is parsed for <code>type</code>, <code>confidence</code>, and a free-text <code>fix</code> recommendation.</li>
                    </ol>
                    <p>The <code>detect_anomalies_task</code> walks services in batches of 100. For each service, it:</p>
                    <ol>
                        <li>Fetches the latest deployment&apos;s <code>build_logs</code> (or <code>None</code>).</li>
                        <li>Falls back to the service&apos;s <code>health_status</code> if there are no logs.</li>
                        <li>Runs <code>LogAnalyzer.analyze_logs()</code>.</li>
                        <li>For each issue with <code>confidence &gt;= 0.9</code>, calls <code>RemediationEngine.apply_fix(issue_type, service_id)</code>.</li>
                        <li>Logs the result to <code>AuditLog</code>.</li>
                    </ol>

                    <h2 id="self-healing" className="text-2xl font-bold flex items-center gap-2">
                        <Brain className="w-5 h-5 text-cyan-600" /> Self-Healing
                    </h2>
                    <p>The <code>RemediationEngine</code> knows about a set of remediation actions. Each is a <code>(action, resource, message, [amount])</code> tuple. The actions are pre-conditions for the side-effect they trigger.</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Issue</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Action</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Side effect</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">OOM_KILLED</td><td className="p-3">SCALE_UP (MEMORY +256MB)</td><td className="p-3">Increments <code>memory_mb</code> by 256 (capped at 2048), then triggers a re-deploy.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DB_CONNECTION_TIMEOUT</td><td className="p-3">SCALE_UP_POOL</td><td className="p-3">Emits an <code>AuditLog</code> with the recommended fix; does not auto-apply.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">CRASH_LOOP</td><td className="p-3">ROLLBACK</td><td className="p-3">Finds the most recent <code>ACTIVE</code> deployment and triggers an instant-rollback.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">SSL_CERT_EXPIRED</td><td className="p-3">NOTIFY_ADMIN</td><td className="p-3">Emits an admin notification; Caddy-managed certs auto-renew.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DISK_FULL</td><td className="p-3">CLEANUP</td><td className="p-3">Runs <code>docker system prune -f</code>. <strong>Gated by <code>explicit_admin=True</code>.</strong></td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">PORT_CONFLICT</td><td className="p-3">RESTART</td><td className="p-3">Issues a Docker restart on the running container.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DNS_FAILURE</td><td className="p-3">NOTIFY_ADMIN</td><td className="p-3">Emits an admin notification.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">DEPENDENCY_MISSING</td><td className="p-3">REBUILD</td><td className="p-3">Triggers a fresh deploy (with cache invalidation).</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">BUILD_FAILURE</td><td className="p-3">NOTIFY_AND_DIAGNOSE</td><td className="p-3">Generates an AI diagnosis and writes it to <code>Deployment.ai_diagnosis</code>.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">TIMEOUT</td><td className="p-3">SCALE_UP (REPLICAS +1)</td><td className="p-3">Increments <code>min_replicas</code> by 1, then triggers a re-deploy.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-cyan-600 dark:text-cyan-400">HEALTH_CHECK_FAIL</td><td className="p-3">RESTART_OR_ROLLBACK</td><td className="p-3">First attempts a Docker restart; if the service is still unhealthy on the next scan, triggers a rollback.</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>The engine is <strong>cooldown-aware</strong> for auto-deploys. After a remediation triggers a re-deploy, no further re-deploy is triggered for the same service within <code>AUTO_DEPLOY_COOLDOWN_MINUTES = 10</code> minutes. This prevents oscillation when the same issue repeats in consecutive scans.</p>
                    <p>The <code>Service.last_scale_at</code> field (see <Link href="/docs/autoscaling" className="text-cyan-600 dark:text-cyan-400 hover:underline">Autoscaling</Link>) is also respected: any scaling action updates <code>last_scale_at</code>, and the autoscaler&apos;s 1-minute cooldown is applied across all scale changes.</p>

                    <h3>The <code>explicit_admin</code> Gate</h3>
                    <p>Two of the side effects — <code>CLEANUP</code> (the <code>docker system prune</code> call) and certain ad-hoc notifications — are destructive or external. The engine refuses to run them unless <code>explicit_admin=True</code> is passed to <code>apply_fix()</code>. The proactive scan (<code>proactive_health_scan_task</code>) and the anomaly scan (<code>detect_anomalies_task</code>) <strong>never</strong> pass <code>explicit_admin=True</code>. Only the admin&apos;s manual &quot;fix this now&quot; action in the UI (or a direct API call) can trigger these actions.</p>
                    <p>This is enforced at the engine level: the <code>CLEANUP</code> action returns <code>False</code> immediately if <code>explicit_admin</code> is falsy, and logs a warning. The platform&apos;s <code>docker system prune</code> cache key (<code>docker_prune:&lt;server_id&gt;</code>) is set after a successful prune and prevents re-running the command within 24 hours.</p>

                    <h3>Service-Locking</h3>
                    <p>The engine uses <code>SELECT … FOR UPDATE</code> on the <code>Service</code> row before applying any fix. This is necessary because the anomaly scan and the proactive scan can run concurrently on the same service — without a row lock, two scans could observe the same issue and each trigger a re-deploy, doubling the remediation work. The lock is held for the duration of the <code>apply_fix()</code> call.</p>

                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-cyan-600" /> Security
                    </h2>

                    <h3>Docker Pruning Is Gated</h3>
                    <p><code>docker system prune -f</code> removes all stopped containers, dangling images, and unused networks. The action is destructive: any in-flight <code>BUILDING</code> deployment that depends on a removed image will fail.</p>
                    <p>The engine only runs the prune when <code>explicit_admin=True</code> AND no prune has been issued for the same <code>server_id</code> in the last 24 hours. The 24-hour cooldown is enforced via <code>cache.set(&quot;docker_prune:&lt;server_id&gt;&quot;, now, DOCKER_PRUNE_COOLDOWN_SECONDS)</code>.</p>
                    <p>The prune is run via <code>subprocess.run([&apos;docker&apos;, &apos;system&apos;, &apos;prune&apos;, &apos;-f&apos;], timeout=30, check=True)</code>. The 30-second timeout prevents a stuck prune from blocking the Celery worker. The <code>check=True</code> ensures a non-zero exit is raised as an exception, which the engine catches and turns into a <code>False</code> return.</p>

                    <h3>Audit Trail</h3>
                    <p>Every remediation action writes an <code>AuditLog</code> row. The chain is:</p>
                    <ol>
                        <li><code>actor = &quot;AI_REMEDIATOR&quot;</code>.</li>
                        <li><code>action</code> is the action name (<code>SCALE_UP</code>, <code>CLEANUP</code>, <code>REBUILD</code>, <code>RESTART</code>, <code>NOTIFY_ADMIN</code>, …).</li>
                        <li><code>target</code> is the service name (or <code>&quot;SYSTEM&quot;</code> for platform-wide actions like <code>CLEANUP</code>).</li>
                        <li><code>metadata</code> includes the old / new values, the reason, and any side-effect details (e.g. PR URLs from Jules).</li>
                    </ol>
                    <p>Because the chain is hash-linked (see <code>AuditLog.save()</code> in <code>models_audit.py</code>), the audit trail cannot be tampered with retroactively.</p>

                    <h2 id="api-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-cyan-600" /> API Reference
                    </h2>
                    <p>The intelligence layer exposes a small set of read-only API endpoints. There are no write endpoints for periodic tasks (they run on Celery beat); the only user actions are &quot;view&quot; and &quot;trigger scan&quot;.</p>
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
                                <tr><td className="p-3 font-mono">/api/v1/ai/report/</td><td className="p-3">GET</td><td className="p-3">Most recent <code>DAILY_REPORT</code> row.</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/ai/anomalies/</td><td className="p-3">GET</td><td className="p-3">Last 100 <code>AuditLog</code> rows with <code>actor in [&apos;AI_REMEDIATOR&apos;, &apos;AI_REVIEWER&apos;]</code>.</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/ai/cost-estimate/</td><td className="p-3">POST</td><td className="p-3">Estimates the LLM cost of a proposed prompt (no actual call).</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/ai/analyze/</td><td className="p-3">POST</td><td className="p-3">Run a one-shot log analysis on a deployment.</td></tr>
                                <tr><td className="p-3 font-mono">/api/v1/ai/jules-history/&#123;service_id&#125;/</td><td className="p-3">GET</td><td className="p-3">Returns the auto-fix history for a service.</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>These endpoints are throttled by <code>AIAnalysisRateThrottle</code> (10/minute per user). They are not admin-gated; any authenticated user can read the report and anomaly history.</p>

                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-cyan-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;Daily intelligence report did not generate at 06:00 UTC&quot;</h3>
                    <p>Celery beat is not running, or the platform&apos;s timezone is misconfigured. The task is registered with a fixed <code>06:00 UTC</code> cron; if the platform&apos;s <code>TIME_ZONE</code> is not UTC, the report will still be generated at 06:00 UTC and stored under the UTC date. Verify with:</p>
                    <CodeBlock>{`docker exec smsly-hosting-backend-1 python manage.py shell \\
  -c "from apps.intelligence.tasks import daily_intelligence_report_task; print(daily_intelligence_report_task)"`}</CodeBlock>

                    <h3>&quot;Anomaly scan returned 0 issues_detected across 247 services&quot;</h3>
                    <p>This can be normal if the platform has been running quietly. If you expect issues, check the platform&apos;s log shipping — Loki / Promtail must be running, and the <code>prometheus_loki</code> integration must be configured for the <code>LogAnalyzer</code> to receive runtime log streams.</p>

                    <h3>&quot;RemediationEngine refused to run docker system prune&quot;</h3>
                    <p><code>explicit_admin</code> was not passed. The scan path never passes it. Trigger the prune manually from the UI (Settings → Storage → Cleanup) or call <code>RemediationEngine().apply_fix(&apos;DISK_FULL&apos;, service_id, explicit_admin=True)</code> from a Django shell.</p>

                    <h3>&quot;Remediation triggered a re-deploy that is still running. The next scan skipped the service.&quot;</h3>
                    <p>This is the auto-deploy cooldown. After a remediation triggers a re-deploy, the same service will not be re-deployed by the engine for 10 minutes. Wait for the cooldown, or for the active deploy to reach a terminal state.</p>

                    <h3>&quot;I disabled AI in Settings but the periodic scans still run&quot;</h3>
                    <p>That is expected. The intelligence scans do not require an LLM — they fall back to rule-only mode when no provider is configured. To disable the scans entirely, set <code>INTELLIGENCE_DISABLED=True</code> in <code>.env</code> and restart the backend and beat scheduler.</p>

                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs/ai" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> AI &amp; Intelligence
                        </Link>
                        <Link href="/docs/functions" className="flex items-center gap-1.5 text-sm text-cyan-600 dark:text-cyan-400 hover:underline font-medium">
                            Functions <ArrowRight size={14} />
                        </Link>
                    </div>

                </article>
            </div>
        </main>
    );
}
