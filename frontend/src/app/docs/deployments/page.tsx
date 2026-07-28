'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Rocket, BookOpen, GitBranch, ListChecks, Server, Code2, Copy, Check, Shield, Wrench, Cog, Globe, Webhook, Activity } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'deployment-types', label: 'Deployment Types', icon: GitBranch },
    { id: 'build-phases', label: 'Build Phases', icon: Cog },
    { id: 'status-reference', label: 'Status Reference', icon: Activity },
    { id: 'api-reference', label: 'API Reference', icon: Code2 },
    { id: 'webhook-setup', label: 'Webhook Setup', icon: Webhook },
    { id: 'buildpacks', label: 'Buildpacks', icon: Server },
    { id: 'env-vars', label: 'Environment Variables', icon: ListChecks },
    { id: 'health-checks', label: 'Health Checks', icon: Activity },
    { id: 'autoscaler', label: 'Autoscaler Interaction', icon: Activity },
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

export default function DeploymentsDocsPage() {
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

            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-indigo-50/60 to-white dark:from-indigo-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-indigo-600 dark:text-indigo-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-indigo-100 dark:bg-indigo-900/50 rounded-xl">
                            <Rocket className="w-5 h-5 text-indigo-700 dark:text-indigo-300" />
                        </div>
                        <span className="text-sm font-semibold text-indigo-600 dark:text-indigo-400 uppercase tracking-wider">Deployments</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Deployments
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        Source to running container. Git, Docker, upload, template, or inline function. Every step observable, audit-logged, rollback-safe.
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
                                            ? 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 font-semibold'
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
                        <BookOpen className="w-5 h-5 text-indigo-600" /> Overview
                    </h2>
                    <p>
                        A <strong>deployment</strong> is one attempt to promote a new revision of a service. Each deployment has a single status that advances through a fixed set of states. Deployments are asynchronous: the API returns the new record immediately and a Celery worker drives it through the pipeline.
                    </p>
                    <p>Common reasons to use deployments:</p>
                    <ul>
                        <li>Ship a new commit to a running service.</li>
                        <li>Roll back a broken release to the last <code>ACTIVE</code> revision.</li>
                        <li>Wire a Git provider to deploy on every push.</li>
                        <li>Promote a tagged release to production.</li>
                        <li>Re-run the pipeline after a settings change, env var update, or build-config tweak.</li>
                    </ul>
                    <p>Deployments always run in the context of a <code>Service</code>. A service has a <code>deploy_type</code> (<code>GIT</code>, <code>DOCKER</code>, <code>UPLOAD</code>, <code>TEMPLATE</code>, or <code>FUNCTION</code>) that determines how the pipeline is wired.</p>

                    <h2 id="deployment-types" className="text-2xl font-bold flex items-center gap-2">
                        <GitBranch className="w-5 h-5 text-indigo-600" /> Deployment Types
                    </h2>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">deploy_type</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Source of truth</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">When to use</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">GIT</td>
                                    <td className="p-3">A Git repository (GitHub, GitLab, Bitbucket) reachable from the build agent.</td>
                                    <td className="p-3">The common case: your application lives in a Git repo.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">DOCKER</td>
                                    <td className="p-3">A pre-built image reference (e.g. <code>ghcr.io/org/app:abc1234</code>).</td>
                                    <td className="p-3">You build images elsewhere (CI, local Docker) and want Grid to host them.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">UPLOAD</td>
                                    <td className="p-3">A source tarball uploaded through the API.</td>
                                    <td className="p-3">One-off deploys, prototypes, environments without a Git provider.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">TEMPLATE</td>
                                    <td className="p-3">A one-click template from the Grid catalog.</td>
                                    <td className="p-3">Spinning up Postgres + Redis + app stacks with a few clicks.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">FUNCTION</td>
                                    <td className="p-3">Inline source code stored on the <code>Service</code> row.</td>
                                    <td className="p-3">See <Link href="/docs/functions" className="text-indigo-600 dark:text-indigo-400 hover:underline">Functions</Link> for the serverless workflow.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <h2 id="build-phases" className="text-2xl font-bold flex items-center gap-2">
                        <Cog className="w-5 h-5 text-indigo-600" /> Build Phases
                    </h2>
                    <p>A <code>GIT</code> deployment passes through seven observable phases. The phase name is the <code>pipeline_stages</code> entry, and the deployment&apos;s <code>status</code> reflects the dominant phase.</p>
                    <div className="bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl p-5 my-4 not-prose">
                        <pre className="text-xs text-slate-700 dark:text-slate-300 font-mono leading-relaxed">{`QUEUED  →  REVIEW  →  BUILDING  →  PUSH  →  DEPLOYING  →  HEALTH_CHECK  →  ACTIVE
                          │            │           │              │
                          └─ BUILD_FAILED   PUSH_FAILED DEPLOY_FAILED HEALTH_FAILED → FAILED`}</pre>
                    </div>
                    <ol>
                        <li><strong>Clone</strong> — shallow <code>git fetch --depth=1</code> to the commit hash, into <code>build_&lt;deployment_id&gt;_*</code>.</li>
                        <li><strong>Analyze</strong> — reads <code>package.json</code>, <code>pyproject.toml</code>, <code>requirements.txt</code>, <code>Dockerfile</code>, <code>nixpacks.toml</code>. The output is <code>Deployment.review_summary</code>. Fresh <code>GIT</code> deploys pause at <code>REVIEW</code>.</li>
                        <li><strong>Build</strong> — the chosen buildpack (<code>NIXPACKS</code>, <code>DOCKER</code>, or <code>STATIC</code>) produces a container image.</li>
                        <li><strong>Push</strong> — image is pushed to the local insecure registry on <code>MASTER_MESH_IP:5000</code> on multi-node fleets. Single-node: image is loaded into the local Docker daemon.</li>
                        <li><strong>Deploy</strong> — new container started. The strategy (<code>ROLLING</code>, <code>BLUE_GREEN</code>, or <code>CANARY</code>) is set on the service.</li>
                        <li><strong>Health check</strong> — Traefik sends <code>GET &lt;health_check_path&gt;</code> at <code>health_check_interval</code> (default 30s).</li>
                        <li><strong>Active</strong> — new container is now serving traffic. All other <code>ACTIVE</code> deployments for the same service are demoted to <code>INACTIVE</code>.</li>
                    </ol>

                    <h2 id="status-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Activity className="w-5 h-5 text-indigo-600" /> Status Reference
                    </h2>
                    <p>Every deployment carries a single <code>status</code> value. The list below covers all defined statuses; the most common ones are bolded.</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Status</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Phase</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Terminal?</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono font-medium">QUEUED</td><td className="p-3">initial</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">REVIEW</td><td className="p-3">analyze</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">BUILDING</td><td className="p-3">build</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">BUILD_FAILED</td><td className="p-3">build</td><td className="p-3 font-semibold text-red-600 dark:text-red-400">Yes</td></tr>
                                <tr><td className="p-3 font-mono font-medium">AWAITING_APPROVAL</td><td className="p-3">review</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">BACKUP_RUNNING</td><td className="p-3">pre-deploy</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">BACKUP_FAILED</td><td className="p-3">pre-deploy</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">MIGRATION_PLANNING</td><td className="p-3">pre-deploy</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">MIGRATION_RUNNING</td><td className="p-3">pre-deploy</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">MIGRATION_FAILED</td><td className="p-3">pre-deploy</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">DEPLOYING</td><td className="p-3">deploy</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium">HEALTH_CHECK</td><td className="p-3">health</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-emerald-600 dark:text-emerald-400">ACTIVE</td><td className="p-3">success</td><td className="p-3 font-semibold text-emerald-600 dark:text-emerald-400">Yes (lifecycle)</td></tr>
                                <tr><td className="p-3 font-mono font-medium">INACTIVE</td><td className="p-3">post-success</td><td className="p-3 font-semibold">Yes (lifecycle)</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-red-600 dark:text-red-400">FAILED</td><td className="p-3">any</td><td className="p-3 font-semibold text-red-600 dark:text-red-400">Yes</td></tr>
                                <tr><td className="p-3 font-mono font-medium">CANCELLED</td><td className="p-3">any</td><td className="p-3 font-semibold">Yes</td></tr>
                                <tr><td className="p-3 font-mono font-medium">ROLLING_BACK</td><td className="p-3">any</td><td className="p-3 text-slate-500">No</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-amber-600 dark:text-amber-400">ROLLED_BACK</td><td className="p-3">terminal</td><td className="p-3 font-semibold text-amber-600 dark:text-amber-400">Yes</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>
                        <code>BUILDING</code>, <code>DEPLOYING</code>, <code>HEALTH_CHECK</code>, <code>BACKUP_RUNNING</code>, <code>MIGRATION_RUNNING</code>, and <code>ROLLING_BACK</code> are the <strong>active</strong> statuses. A service can only have one active deployment at a time; creating a second one returns HTTP 409 with the existing deployment in the response body.
                    </p>

                    <h2 id="api-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-indigo-600" /> API Reference
                    </h2>
                    <p>All endpoints are mounted under <code>/api/v1/</code>. Authentication is session- or token-based for user endpoints and HMAC-signed for node-to-node traffic.</p>

                    <h3>Trigger a deployment</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/deployments/trigger/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "provider_id": "f1c2b0c1-1234-5678-9abc-def012345678",
    "commit_hash": "abc1234"
  }'`}</CodeBlock>
                    <p>Returns HTTP 201 with the new deployment record and <code>status=QUEUED</code>.</p>

                    <h3>Cancel a deployment</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/deployments/2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a/cancel/ \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>
                    <p>Allowed only when the deployment is in <code>QUEUED</code>, <code>REVIEW</code>, <code>BUILDING</code>, or <code>AWAITING_APPROVAL</code>.</p>

                    <h3>Approve a paused deployment</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/deployments/2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a/approve/ \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>

                    <h3>Roll back a deployment</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/deployments/2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a/rollback/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"confirm": "true"}'`}</CodeBlock>
                    <p>The <code>confirm: &quot;true&quot;</code> gate prevents accidental rollbacks. The endpoint creates a <strong>new</strong> deployment row with <code>is_rollback=True</code>.</p>

                    <h3>One-click rollback</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/instant-rollback/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"message": "5xx spike after deploy"}'`}</CodeBlock>
                    <p>Looks up the most recent <code>ACTIVE</code> deployment and rolls back to it. The caller does not need to know the deployment ID.</p>

                    <div className="not-prose mt-4 p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-start gap-3">
                        <Code2 className="w-5 h-5 text-indigo-600 dark:text-indigo-400 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">Full API reference</p>
                            <p className="text-sm text-slate-600 dark:text-slate-400">
                                See <Link href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/docs/deployments.md" className="text-indigo-600 dark:text-indigo-400 hover:underline font-medium">docs/deployments.md</Link> in the repository for every endpoint, request body, response field, and error code — including <code>/api/v1/deployments/&#123;id&#125;/rollback/</code>, <code>instant-rollback/</code>, and the multi-server <code>deploy/</code> / <code>multi-deploy/</code> actions.
                            </p>
                        </div>
                    </div>

                    <h2 id="webhook-setup" className="text-2xl font-bold flex items-center gap-2">
                        <Webhook className="w-5 h-5 text-indigo-600" /> Webhook Setup
                    </h2>
                    <p>Grid accepts webhooks from GitHub, GitLab, and Bitbucket. Each delivery creates a deployment for the matching service, and the webhook handler is idempotent: a <code>WebhookDelivery</code> row is keyed on the provider&apos;s <code>delivery_id</code>, so duplicate deliveries are dropped.</p>

                    <h3>GitHub</h3>
                    <ol>
                        <li>In your repo, go to <strong>Settings → Webhooks → Add webhook</strong>.</li>
                        <li>Set <strong>Payload URL</strong> to <code>https://&lt;your-grid-host&gt;/api/v1/webhooks/github/</code>.</li>
                        <li>Set <strong>Content type</strong> to <code>application/json</code>.</li>
                        <li>Set <strong>Secret</strong> to the same value as <code>GITHUB_WEBHOOK_SECRET</code> in the Grid <code>.env</code>.</li>
                        <li>Choose <strong>Let me select individual events</strong> and enable <code>Push</code> and <code>Pull request</code>.</li>
                        <li>Save. Push to the configured branch to fire a deployment.</li>
                    </ol>

                    <h3>GitLab</h3>
                    <ol>
                        <li><strong>Settings → Webhooks</strong> in the project.</li>
                        <li>URL: <code>https://&lt;your-grid-host&gt;/api/v1/webhooks/gitlab/</code>.</li>
                        <li>Trigger: <strong>Push events</strong> and <strong>Merge request events</strong>.</li>
                        <li>Set the <strong>Secret token</strong> to <code>GITLAB_WEBHOOK_SECRET</code>.</li>
                    </ol>

                    <h3>Bitbucket</h3>
                    <ol>
                        <li><strong>Repository settings → Webhooks → Add webhook</strong>.</li>
                        <li>URL: <code>https://&lt;your-grid-host&gt;/api/v1/webhooks/bitbucket/</code>.</li>
                        <li>Triggers: <strong>Repo: push</strong> and <strong>Pull request: created / updated</strong>.</li>
                    </ol>

                    <h2 id="buildpacks" className="text-2xl font-bold flex items-center gap-2">
                        <Server className="w-5 h-5 text-indigo-600" /> Buildpacks
                    </h2>
                    <p>A service&apos;s <code>buildpack</code> field selects the build strategy. The default is <code>NIXPACKS</code>.</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Buildpack</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Behavior</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">NIXPACKS</td><td className="p-3">Detects the language and emits a multi-stage Dockerfile. Supports Node, Python, Go, Ruby, Rust, Java, PHP, Elixir, Deno, Bun.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">DOCKER</td><td className="p-3">Uses the <code>Dockerfile</code> at the service&apos;s <code>root_directory</code> (default <code>/</code>).</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-indigo-600 dark:text-indigo-400">STATIC</td><td className="p-3">Serves the directory as a static site. Traefik routes <code>/</code> to a small nginx container.</td></tr>
                            </tbody>
                        </table>
                    </div>

                    <h2 id="env-vars" className="text-2xl font-bold flex items-center gap-2">
                        <ListChecks className="w-5 h-5 text-indigo-600" /> Environment Variables
                    </h2>
                    <p><code>Service.env_vars</code> is a list of <code>(key, value, is_secret, is_locked, source)</code> rows. The values are stored as <code>EncryptedCharField</code> and decrypted at deploy time.</p>
                    <h3>Precedence</h3>
                    <p>The final env on the new container is the union of these sources, in this order (later overrides earlier):</p>
                    <ol>
                        <li><strong>Platform defaults</strong> — <code>PORT</code>, <code>SMSLY_API_KEY</code>, <code>SMSLY_PUBLIC_DOMAIN</code>.</li>
                        <li><strong>Addon auto-injection</strong> — <code>source=ADDON</code>.</li>
                        <li><strong>Shortcode resolution</strong> — <code>source=SHORTCODE</code>. Example: <code>&#123;&#123;pg.MAIN.DATABASE_URL&#125;&#125;</code>.</li>
                        <li><strong>System auto-injection</strong> — <code>source=SYSTEM</code>. Includes <code>DEPLOYMENT_ID</code>, <code>COMMIT_HASH</code>, <code>BRANCH</code>, <code>SERVICE_NAME</code>.</li>
                        <li><strong>User-defined</strong> — <code>source=USER</code>. Highest precedence.</li>
                    </ol>
                    <p>If a user-defined row is marked <code>is_locked=True</code>, it cannot be overridden by any auto-injection step.</p>

                    <h2 id="health-checks" className="text-2xl font-bold flex items-center gap-2">
                        <Activity className="w-5 h-5 text-indigo-600" /> Health Checks and Auto-Restart
                    </h2>
                    <p>Each service has its own health check config:</p>
                    <ul>
                        <li><code>health_check_path</code> (default <code>/health</code>)</li>
                        <li><code>health_check_port</code> (blank = auto-detect from <code>PORT</code> env)</li>
                        <li><code>health_check_interval</code> (default 30s)</li>
                        <li><code>health_check_timeout</code> (default 300s)</li>
                        <li><code>health_check_retries</code> (default 90)</li>
                        <li><code>auto_restart</code> (default <code>True</code>)</li>
                        <li><code>restart_policy</code> (<code>always</code>, <code>unless-stopped</code>, <code>on-failure</code>, <code>no</code>)</li>
                    </ul>
                    <p>Containers can also push their own health status via the <strong>Service Health Webhook</strong>:</p>
                    <CodeBlock>{`curl -X POST https://<your-grid-host>/api/v1/services/<service-id>/health/webhook/ \\
  -H "X-Health-Webhook-Token: <service.health_webhook_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"status": "healthy", "details": {"db": "ok", "cache": "ok"}}'`}</CodeBlock>
                    <p>Accepted <code>status</code> values: <code>healthy</code>, <code>unhealthy</code>, <code>starting</code>, <code>needs_manual_intervention</code>.</p>

                    <h2 id="autoscaler" className="text-2xl font-bold flex items-center gap-2">
                        <Cog className="w-5 h-5 text-indigo-600" /> Autoscaler Interaction
                    </h2>
                    <p>The autoscaler can mutate <code>Service.min_replicas</code> while a deploy is in flight. To prevent the deploy&apos;s container plan from drifting, the platform snapshots <code>min_replicas</code> onto the deployment row at queue time as <code>Deployment.queued_min_replicas</code>. The deploy executor uses this snapshot to decide how many containers to bring up at deploy time, not the live <code>min_replicas</code> field.</p>
                    <p>This means:</p>
                    <ul>
                        <li>If a user triggers a deploy and the autoscaler is concurrently scaling up, the new deploy starts with the smaller count and the autoscaler brings the extra replicas online a few seconds later.</li>
                        <li>If the autoscaler is concurrently scaling down, the new deploy starts with the larger count and the autoscaler schedules a scale-down after its cooldown elapses.</li>
                    </ul>
                    <p>See <Link href="/docs/autoscaling" className="text-indigo-600 dark:text-indigo-400 hover:underline">Autoscaling</Link> for the full replica controller design.</p>

                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-indigo-600" /> Security
                    </h2>
                    <h3>Deployment Throttles</h3>
                    <p>The <code>DeploymentViewSet</code> is gated by two DRF throttles:</p>
                    <ul>
                        <li><code>BurstRateThrottle</code> — <code>3/minute</code> per user. Prevents rapid-fire re-triggers.</li>
                        <li><code>DeploymentRateThrottle</code> — <code>10/hour</code> per user. Prevents resource exhaustion from excessive builds.</li>
                    </ul>
                    <p>Both return HTTP 429 with a <code>Retry-After</code> header.</p>

                    <h3>Audit Log</h3>
                    <p>Every state change on a deployment writes an <code>AuditLog</code> row. The chain is hash-linked — see the <code>AuditLog.calculate_hash()</code> and <code>AuditLog.save()</code> overrides in <code>models_audit.py</code>. Logs are immutable.</p>
                    <p>Common audit events emitted by the pipeline:</p>
                    <ul>
                        <li><code>DEPLOYMENT_TRIGGER</code> — user triggered a new deployment.</li>
                        <li><code>DEPLOYMENT_ROLLBACK</code> — user requested a specific rollback.</li>
                        <li><code>DEPLOYMENT_ROLLBACK_INSTANT</code> — user clicked instant-rollback.</li>
                        <li><code>DEPLOYMENT_APPROVE</code> — user approved a paused deployment.</li>
                        <li><code>DEPLOYMENT_CANCEL</code> — user cancelled a deployment.</li>
                    </ul>

                    <h3>SSRF Protection</h3>
                    <p>The deploy pipeline clones repositories over <code>https://</code> or <code>git://</code>. URLs are validated against <code>_validate_registry_url()</code> which:</p>
                    <ul>
                        <li>Rejects loopback, link-local, multicast, reserved, and unspecified ranges.</li>
                        <li>Accepts private RFC 1918 ranges only when the host resolves to a registered <code>CloudProvider</code>.</li>
                        <li>Rejects non-HTTPS URLs unless the host is in the platform&apos;s <code>localhost</code> / Docker service list.</li>
                    </ul>

                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-indigo-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;Deployment already in progress (status: BUILDING)&quot;</h3>
                    <p>There is an active deployment for this service. Either wait for it to finish or <code>POST /api/v1/deployments/&#123;id&#125;/cancel/</code>. Creating a second active deployment returns HTTP 409 with the existing deployment in <code>existing_deployment</code>.</p>

                    <h3>&quot;Cannot cancel deployment in HEALTH_CHECK status&quot;</h3>
                    <p><code>HEALTH_CHECK</code> is past the cancel boundary. Wait for the deployment to reach <code>ACTIVE</code> or <code>FAILED</code>, then trigger a rollback if needed.</p>

                    <h3>Build hangs in <code>BUILDING</code></h3>
                    <p>The buildpack has stalled — usually a network failure (npm registry down, <code>apt-get update</code> timing out) or a runaway <code>npm install</code> cycle. Inspect <code>GET /api/v1/deployments/&#123;id&#125;/build-logs/</code> for the live log tail.</p>

                    <h3>&quot;BUILD_FAILED: exit 137&quot;</h3>
                    <p>OOM-killed during build. Reduce build memory pressure (move large assets out of the build, use <code>.dockerignore</code>) or raise the platform&apos;s per-task memory limit (see <code>docker-compose.prod.yml</code>).</p>

                    <h3>&quot;ENCRYPTION_KEY_MISMATCH&quot; at restore time</h3>
                    <p>A <code>BACKUP_ENCRYPTION_KEY</code> was rotated without restarting the backend, or the encrypted backup was made on a different installation. Set <code>BACKUP_ENCRYPTION_KEY</code> to the value used at backup time, restart the backend, and re-run the deploy.</p>

                    <h3>Health checks pass on the dashboard but the public domain returns 502</h3>
                    <p>The platform considers the container healthy, but the Traefik route is stale. Force a route re-check: <code>POST /api/v1/services/&#123;id&#125;/recheck-health/</code> and then <code>POST /api/v1/system/route-recheck/</code>.</p>

                    <h3>Webhook deliveries do not trigger deployments</h3>
                    <p>Inspect the <code>WebhookDelivery</code> table — duplicate deliveries are recorded with <code>status=ignored</code>. The most common cause is a webhook signed with a secret that does not match the service owner&apos;s <code>CloudProvider</code> config.</p>

                    <h3>&quot;vulnerability_report is empty after build&quot;</h3>
                    <p>The Trivy scan was skipped. This happens when the image is on a registry that Trivy cannot reach. Configure <code>TRIVY_REGISTRY_USERNAME</code> / <code>TRIVY_REGISTRY_PASSWORD</code> in the platform <code>.env</code> and re-trigger.</p>

                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> All Docs
                        </Link>
                        <Link href="/docs/functions" className="flex items-center gap-1.5 text-sm text-indigo-600 dark:text-indigo-400 hover:underline font-medium">
                            Functions <ArrowRight size={14} />
                        </Link>
                    </div>

                </article>
            </div>
        </main>
    );
}
