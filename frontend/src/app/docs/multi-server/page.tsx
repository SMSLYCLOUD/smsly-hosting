'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Network, BookOpen, Server, Shield, ListChecks, Wrench, GitBranch, Code2, Copy, Check, Activity, Cpu, Database } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'architecture', label: 'Architecture', icon: Network },
    { id: 'node-modes', label: 'Node Modes', icon: GitBranch },
    { id: 'connecting', label: 'Connecting a Server', icon: Server },
    { id: 'api-reference', label: 'API Reference', icon: Code2 },
    { id: 'self-healing', label: 'Self-Healing', icon: Activity },
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

export default function MultiServerDocsPage() {
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

            {/* Hero */}
            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-violet-50/60 to-white dark:from-violet-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-violet-100 dark:bg-violet-900/50 rounded-xl">
                            <Network className="w-5 h-5 text-violet-700 dark:text-violet-300" />
                        </div>
                        <span className="text-sm font-semibold text-violet-600 dark:text-violet-400 uppercase tracking-wider">Multi-Server Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Multi-Server & Remote Deployment
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        Span a primary, full-stack followers, and lightweight agents. One dashboard, one WireGuard mesh, one source of truth.
                    </p>
                </div>
            </section>

            <div className="max-w-7xl mx-auto flex gap-8 px-4 py-12">

                {/* Sidebar TOC */}
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
                                            ? 'bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 font-semibold'
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

                {/* Main content */}
                <article className="flex-1 max-w-3xl prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">

                    {/* Overview */}
                    <h2 id="overview" className="text-2xl font-bold flex items-center gap-2 mt-0">
                        <BookOpen className="w-5 h-5 text-violet-600" /> Overview
                    </h2>
                    <p>
                        A <strong>ManagedServer</strong> is the unit of fleet membership. Each remote node is registered, has a status (<code>ONLINE</code> / <code>OFFLINE</code> / <code>UNKNOWN</code>), and may optionally run a self-healing orchestrator that diagnoses and recovers from failures automatically.
                    </p>
                    <p>The platform provides two ways to add a node:</p>
                    <ul>
                        <li><strong>Connect Existing</strong> — you already have a VPS. You bring SSH credentials (and optionally an API URL/token and a gateway secret).</li>
                        <li><strong>Provision New</strong> — you only have SSH credentials. Grid&apos;s <code>install.sh</code> runs over SSH, lays down the platform, and auto-fills <code>api_url</code> / <code>api_token</code>.</li>
                    </ul>
                    <p>Either path produces a <code>ManagedServer</code> row. From that point on, the server is part of the fleet and can be a target for transfers, deployments, and self-healing.</p>

                    <div className="bg-violet-50 dark:bg-violet-900/20 border border-violet-200 dark:border-violet-700 rounded-xl p-4 my-6 not-prose">
                        <p className="text-sm text-violet-800 dark:text-violet-200">
                            <strong>Use multi-server when you need to:</strong> spread workloads across multiple VPSes, repatriate a service that lives on a remote node, mix a control plane with edge nodes, or keep one source of truth while running compute closer to your users.
                        </p>
                    </div>

                    {/* Architecture */}
                    <h2 id="architecture" className="text-2xl font-bold flex items-center gap-2">
                        <Network className="w-5 h-5 text-violet-600" /> Architecture
                    </h2>
                    <p>A Grid fleet is a leader-elected cluster of <code>ManagedServer</code> records, all reading from a <code>MeshNetwork</code> of <code>WireGuardPeer</code> entries. The local primary is the control plane; remote nodes are either full-stack followers or lightweight agents.</p>

                    <h3>Roles at a glance</h3>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Role</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">What it does</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">Master / Controller</td>
                                    <td className="p-3">Control plane. Runs PostgreSQL, Redis, RabbitMQ, Caddy, frontend, and the management API. Holds the source of truth and the leader-election term. Exactly one per cluster.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">Follower (Full Node)</td>
                                    <td className="p-3">Remote <code>ManagedServer</code> that runs the entire platform stack locally — its own Traefik, RabbitMQ, and (optionally) PostgreSQL — but no frontend or Caddy.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">Lite Agent</td>
                                    <td className="p-3">Compute-only worker that does not run a local database. Connects to the master&apos;s PostgreSQL, RabbitMQ, and Redis over the WireGuard mesh.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <h3>Side-by-side comparison</h3>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Property</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Primary</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Follower</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Lite Agent</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-medium">Runs PostgreSQL</td>
                                    <td className="p-3"><Database size={14} className="inline text-emerald-600 dark:text-emerald-400" /> Yes</td>
                                    <td className="p-3">Yes (own)</td>
                                    <td className="p-3 text-slate-500">No (uses master)</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-medium">Runs Caddy</td>
                                    <td className="p-3">Yes</td>
                                    <td className="p-3">No (Traefik)</td>
                                    <td className="p-3">No (Traefik)</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-medium">Accepts workloads</td>
                                    <td className="p-3 text-slate-500">No (control plane)</td>
                                    <td className="p-3">Yes</td>
                                    <td className="p-3">Yes</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-medium">WireGuard mesh</td>
                                    <td className="p-3">Local peer</td>
                                    <td className="p-3">Member</td>
                                    <td className="p-3">Member (mandatory)</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-medium">Cluster role</td>
                                    <td className="p-3"><code>LEADER</code></td>
                                    <td className="p-3"><code>FOLLOWER</code></td>
                                    <td className="p-3"><code>FOLLOWER</code></td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-medium">Connection strategy</td>
                                    <td className="p-3">Direct</td>
                                    <td className="p-3">Token + HMAC V2 fallback</td>
                                    <td className="p-3">Local-DB reads + mesh-VPN upstream</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    {/* Node Modes */}
                    <h2 id="node-modes" className="text-2xl font-bold flex items-center gap-2">
                        <GitBranch className="w-5 h-5 text-violet-600" /> Node Modes
                    </h2>

                    <h3>Primary (Master)</h3>
                    <p>The master is the source of truth and the orchestrator. It is installed by running <code>install.sh</code> with no <code>--mode</code> flag, which produces the default platform stack. The installer writes a <code>NODE_TYPE=master</code> marker into <code>.env</code> and a corresponding <code>ManagedServer</code> row with <code>is_primary=True, allow_user_workloads=False</code>.</p>
                    <p>The master hosts the WireGuard <code>default</code> mesh as the local peer, issues API tokens and gateway secrets, owns the leader-election term, and holds the encryption keys used by all nodes (Fernet-encrypted credentials, <code>BACKUP_ENCRYPTION_KEY</code>).</p>

                    <h3>Follower (Full-Stack Node)</h3>
                    <p>A follower is a <code>ManagedServer</code> with <code>is_primary=False, is_lite_agent=False, allow_user_workloads=True</code>. It runs its own Docker Compose stack using <code>docker-compose.prod.yml</code> (no frontend, no Caddy) and serves containers via Traefik on port 80.</p>
                    <p>Use followers when the remote VPS has enough resources to run its own database and broker, when each region should be self-contained for performance or data-residency reasons, or when you are running a multi-tenant fleet and want to isolate tenants onto dedicated hosts.</p>

                    <h3>Lite Agent</h3>
                    <p>A Lite Agent is a <code>ManagedServer</code> with <code>is_lite_agent=True</code>. It runs <code>docker-compose.agent-lite.yml</code>: a subset of the platform that includes the backend, worker, and a local Redis/RabbitMQ, but <strong>not</strong> PostgreSQL. The agent&apos;s database connection points at the master over the WireGuard mesh (<code>MASTER_MESH_IP</code>), and its reads (services, deployments) hit the shared master database directly rather than through a proxy.</p>
                    <p>Use Lite Agents when the remote VPS is small (1-2 vCPU, 1-2 GB RAM) and you do not want to run PostgreSQL on it, when the agent is in a private subnet and can reach the master over WireGuard but not the public internet, or when you want to add a node quickly without provisioning a database.</p>

                    {/* Connecting a Server */}
                    <h2 id="connecting" className="text-2xl font-bold flex items-center gap-2">
                        <Server className="w-5 h-5 text-violet-600" /> Connecting a Server
                    </h2>

                    <h3>Connect an existing VPS (UI)</h3>
                    <ol>
                        <li>Open <strong>Servers</strong> in the sidebar and click <strong>Connect Existing</strong>.</li>
                        <li>Enter a friendly name, the public IP or domain, and (optionally) the private IP for the WireGuard endpoint.</li>
                        <li>Choose an auth strategy: <strong>API + token</strong>, <strong>API + gateway secret (HMAC)</strong>, or <strong>SSH only</strong>.</li>
                        <li>Set <code>is_primary=False</code> (the default) and <code>allow_user_workloads=True</code> to make the node a workload target.</li>
                        <li>Submit. A background thread runs a health refresh: probes candidate API URLs, detects the platform version, exchanges a token if needed, and updates status and WireGuard mesh membership.</li>
                    </ol>

                    <h3>Provision a new VPS (UI)</h3>
                    <ol>
                        <li>Open <strong>Servers</strong> and click <strong>Provision New</strong>.</li>
                        <li>Enter name, public IP, SSH port, SSH user, and either a password or a PEM-encoded private key.</li>
                        <li>Optionally toggle <code>is_lite_agent=True</code> to install the agent-lite compose profile instead of the full stack.</li>
                        <li>Submit. The installer runs over SSH, lays down the platform, and auto-fills <code>api_url</code> and <code>api_token</code> on the server record.</li>
                    </ol>

                    <h3>Connect an existing VPS (API)</h3>
                    <CodeBlock>{`curl -sS http://localhost:8000/api/v1/servers/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Worker EU",
    "host": "203.0.113.10",
    "private_ip": "10.0.5.10",
    "api_url": "http://203.0.113.10:8090",
    "api_token": "smsly_…",
    "ssh_user": "root",
    "ssh_password": "REDACTED",
    "is_primary": false,
    "allow_user_workloads": true
  }'`}</CodeBlock>

                    <h3>Provision a new VPS (API)</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/servers/provision/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Worker US",
    "host": "198.51.100.20",
    "ssh_user": "root",
    "ssh_auth_method": "key",
    "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\\n…\\n-----END OPENSSH PRIVATE KEY-----",
    "is_primary": false,
    "allow_user_workloads": true,
    "is_lite_agent": true
  }'`}</CodeBlock>

                    <div className="bg-violet-50 dark:bg-violet-900/20 border border-violet-200 dark:border-violet-700 rounded-xl p-4 my-6 not-prose">
                        <p className="text-sm text-violet-800 dark:text-violet-200">
                            <strong>Prerequisites:</strong> Root SSH access, TCP/22 reachable from the master, a supported Linux distribution (Ubuntu 20.04 / 22.04 / 24.04 LTS), and at least 2 vCPU / 4 GB RAM for a follower (1 vCPU / 1 GB RAM for a Lite Agent).
                        </p>
                    </div>

                    <div className="not-prose mt-4 p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-start gap-3">
                        <Code2 className="w-5 h-5 text-violet-600 dark:text-violet-400 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">Full API reference</p>
                            <p className="text-sm text-slate-600 dark:text-slate-400">
                                See <Link href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/docs/multi-server.md" className="text-violet-600 dark:text-violet-400 hover:underline font-medium">docs/multi-server.md</Link> in the repository for every endpoint, request body, response field, and error code — including the <code>proxy/</code>, <code>heal/</code>, <code>diagnostics/</code>, and <code>run_command/</code> endpoints.
                            </p>
                        </div>
                    </div>

                    {/* API Reference */}
                    <h2 id="api-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-violet-600" /> API Reference
                    </h2>
                    <p>All endpoints are mounted under <code>/api/v1/servers/</code>. Authentication is session- or token-based for user endpoints, and HMAC V2-signed for the internal node-to-node sync endpoints. Filter by <code>?status=ONLINE|OFFLINE|UNKNOWN</code> on the list endpoint.</p>

                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Method & Path</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Purpose</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/</td><td className="p-3">List servers. Filter with <code>?status=…</code>.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/</td><td className="p-3">Connect an existing server (Connect Existing).</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/&#123;id&#125;/</td><td className="p-3">Retrieve a server.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">PATCH /servers/&#123;id&#125;/</td><td className="p-3">Partial update (rotate credentials, toggle workloads).</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">DELETE /servers/&#123;id&#125;/</td><td className="p-3">Remove from the fleet.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/provision/</td><td className="p-3">Provision a brand-new node over SSH.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/&#123;id&#125;/provision-logs/</td><td className="p-3">Stream live provisioning logs.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/&#123;id&#125;/retry-provision/</td><td className="p-3">Re-run the idempotent installer.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/&#123;id&#125;/update-server/</td><td className="p-3">Run the installer for an in-place update.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/&#123;id&#125;/health_check/</td><td className="p-3">Probe a single server&apos;s API.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/check_all/</td><td className="p-3">Health probe every server.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/&#123;id&#125;/proxy/</td><td className="p-3">Forward a generic API request to a remote.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/&#123;id&#125;/services/</td><td className="p-3">List services on a managed server.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/&#123;id&#125;/deployments/</td><td className="p-3">List recent deployments on a managed server.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/&#123;id&#125;/domains/</td><td className="p-3">Aggregate custom domains on a managed server.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/&#123;id&#125;/heal/</td><td className="p-3">Trigger self-healing (action: restart, diagnose, full).</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">GET /servers/&#123;id&#125;/diagnostics/</td><td className="p-3">Read-only diagnostics snapshot.</td></tr>
                                <tr><td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">POST /servers/&#123;id&#125;/run_command/</td><td className="p-3">Run an allow-listed diagnostic command over SSH.</td></tr>
                            </tbody>
                        </table>
                    </div>

                    <h3>Example: full node heal</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/heal/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "action": "full" }'`}</CodeBlock>

                    {/* Self-Healing */}
                    <h2 id="self-healing" className="text-2xl font-bold flex items-center gap-2">
                        <Activity className="w-5 h-5 text-violet-600" /> Self-Healing
                    </h2>
                    <p>The self-healing orchestrator classifies failures into <code>FailureType</code> enums and chooses a <code>RecoveryAction</code>. For node-level heals, the user-facing action is mapped to the orchestrator&apos;s recovery surface.</p>

                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">User-facing action</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Internal action</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">What it does</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium"><Cpu size={14} className="inline text-violet-600 mr-1" />restart_container</td>
                                    <td className="p-3 font-mono text-slate-500">RESTART_CONTAINER</td>
                                    <td className="p-3"><code>docker restart &lt;container&gt;</code> and re-checks after 20s.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">restart_stack</td>
                                    <td className="p-3 font-mono text-slate-500">RESTART_STACK</td>
                                    <td className="p-3"><code>docker compose up -d</code> in <code>/opt/smsly-hosting</code>.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">restart_docker_daemon</td>
                                    <td className="p-3 font-mono text-slate-500">RESTART_DOCKER_DAEMON</td>
                                    <td className="p-3"><code>systemctl restart docker</code> and verifies with <code>docker info</code>.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">diagnose</td>
                                    <td className="p-3 font-mono text-slate-500">—</td>
                                    <td className="p-3">Read-only diagnostics. No recovery.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">full</td>
                                    <td className="p-3 font-mono text-slate-500">RESTART_STACK</td>
                                    <td className="p-3">Node-level: <code>restart_stack</code>. Deployment-level: walks the suggested-action chain and escalates to AI after 5 attempts.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <p>Cooldowns enforce <code>HEAL_COOLDOWN_SECONDS=120</code> (no new heal for the same scope within two minutes) and <code>MAX_HEAL_ATTEMPTS=5</code> (after five attempts the orchestrator returns <code>ESCALATE_TO_AI</code>).</p>

                    {/* Security */}
                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-violet-600" /> Security
                    </h2>
                    <p>The inter-node surface is hardened at five layers.</p>

                    <h3>HMAC V2 signing</h3>
                    <p>Every node-to-node call carries three headers:</p>
                    <ul>
                        <li><code>X-SMSLY-Remote-Sync: 1</code> — declares the request as a node-to-node sync.</li>
                        <li><code>X-Request-Timestamp</code> — UNIX seconds, must be within 300 seconds of the receiver&apos;s clock.</li>
                        <li><code>X-Gateway-Signature-V2</code> — HMAC-SHA256 over <code>METHOD|path|ts|sha256(body)</code> using either the per-node <code>gateway_secret</code> or, as a last-resort fallback, the platform-wide <code>GATEWAY_SECRET</code>. Comparison uses constant-time <code>hmac.compare_digest</code>.</li>
                    </ul>

                    <h3>Token auth</h3>
                    <p>For nodes where an API token has already been exchanged, the dashboard uses <code>Authorization: Token &lt;smsly_…&gt;</code>. Tokens are matched against the SHA-256 hash stored on the <code>APIToken</code> row and are revocable.</p>

                    <h3>Command allow-list</h3>
                    <p><code>POST /run_command/</code> enforces a strict prefix allow-list. Allowed prefixes: <code>docker </code>, <code>cd /opt/smsly-hosting &amp;&amp; docker </code>, <code>df </code>, <code>free </code>, <code>ping </code>, <code>systemctl status docker</code>, and a redacted read of the local <code>.env</code>. Anything else returns 403.</p>

                    <h3>Encrypted credentials</h3>
                    <p><code>api_token</code>, <code>gateway_secret</code>, <code>ssh_password</code>, and <code>ssh_key</code> are all stored in <code>EncryptedCharField</code> / <code>EncryptedTextField</code> (Fernet) on the <code>ManagedServer</code> model. They are never returned by the API. The <code>has_ssh_credentials</code> boolean is the only credential-derived field in the public serializer.</p>

                    <h3>Audit trail</h3>
                    <p>Every meaningful state change is recorded through <code>log_event(...)</code> with a stable action code and a metadata payload. The <code>AuditLog</code> table is hash-chained and protected by <code>BEFORE UPDATE OR DELETE</code> triggers so audit records cannot be silently tampered with.</p>

                    {/* Troubleshooting */}
                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-violet-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;Server &apos;X&apos; is currently OFFLINE. Transfers are only allowed to ONLINE nodes.&quot;</h3>
                    <p>The connected server is registered but the health probe has not received a non-5xx response recently. Run <code>POST /servers/&#123;id&#125;/health_check/</code> and watch for which candidate URL succeeds. The most common causes are a wrong public IP, a firewall blocking port 8090, or the WireGuard mesh not yet converged.</p>

                    <h3>Mesh deploy fails with &quot;WireGuard kernel module is not loaded on the host VPS&quot;</h3>
                    <p>The remote kernel does not have the <code>wireguard</code> module. SSH into the host, run <code>sudo modprobe wireguard</code>, and re-queue the mesh deploy. On hosts without DKMS, the module is provided by the kernel itself on most Ubuntu LTS images; on custom kernels, install <code>wireguard-dkms</code> and reboot.</p>

                    <h3>Token auto-exchange fails with 401 / 403</h3>
                    <p>The remote rejected the bootstrap. Verify that <code>gateway_secret</code> on the source matches <code>GATEWAY_SECRET</code> on the target. If the remote uses credential exchange, ensure <code>ALLOW_REMOTE_PASSWORD_EXCHANGE=1</code> on the <strong>target</strong> and that the SSH password is the admin password.</p>

                    <h3>&quot;Provisioning FAILED — INSTALLATION FAILED&quot;</h3>
                    <p>The remote installer exited non-zero. Open <code>provision_logs</code> for the full stdout. Common causes: an unsupported Linux distribution, no Docker installable, no <code>apt-get</code> or <code>yum</code> present, or insufficient RAM. Re-run with <code>retry-provision</code> after fixing the underlying issue — the script is idempotent.</p>

                    <h3>Self-heal never converges</h3>
                    <p><code>MAX_HEAL_ATTEMPTS=5</code> triggers after the fifth attempt. The orchestrator returns <code>next_action=ESCALATE_TO_AI</code>. If the platform intelligence is configured, the AI Senate analyzes the diagnostic context and proposes commands. Otherwise the heal log is the only artifact — open it from the heal endpoint and address the root cause manually.</p>

                    <h3>A remote node is &quot;ONLINE&quot; but the proxy returns <code>remote_unreachable</code></h3>
                    <p>The health probe found a working base URL, but the proxy candidate-URL rotation tried a different URL and the remote is no longer answering. The proxy falls through the candidate list with multiple auth modes (token, then HMAC, then none) and surfaces <code>remote_unreachable=true</code> with the upstream error. Usually transient — re-run the call.</p>

                    <h3>Domain aggregation truncates at 50 pages</h3>
                    <p>The full-follower implementation paginates through <code>/api/v1/services/</code> with a hard cap of 50 pages. A node with more than 50 pages of services (≥500 services at the default page size) will not have all of its domains listed. Use the per-service <code>/services/</code> endpoint for exhaustive listings, or the master DB directly for Lite Agents.</p>

                    {/* Navigation */}
                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs/transfers" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> Server Transfers
                        </Link>
                        <Link href="/docs/api" className="flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:underline font-medium">
                            API Reference <ArrowRight size={14} />
                        </Link>
                    </div>

                </article>
            </div>
        </main>
    );
}
