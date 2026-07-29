'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, ArrowLeftRight, BookOpen, Server, Shield, ListChecks, Wrench, GitBranch, Code2, Copy, Check } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'transfer-types', label: 'Transfer Types', icon: GitBranch },
    { id: 'prerequisites', label: 'Prerequisites', icon: ListChecks },
    { id: 'how-to-use', label: 'How to Use', icon: ArrowLeftRight },
    { id: 'status-reference', label: 'Status Reference', icon: Server },
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

export default function TransfersDocsPage() {
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
                            <ArrowLeftRight className="w-5 h-5 text-violet-700 dark:text-violet-300" />
                        </div>
                        <span className="text-sm font-semibold text-violet-600 dark:text-violet-400 uppercase tracking-wider">Multi-Server Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Server Transfers
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        Move services between nodes in your Grid fleet. Drag-and-drop in the UI, or drive the pipeline from the API.
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
                        A <strong>server transfer</strong> moves a running workload from one Grid node to another with minimal downtime. The pipeline captures a snapshot of the source service, ships it to the target over SSH, restores it, and (when applicable) updates DNS so traffic follows the container to its new host.
                    </p>
                    <p>Transfers run as background tasks. The API returns a new transfer record immediately; progress and live logs are polled through <code>GET /api/v1/transfers/</code>.</p>
                    <p>Common reasons to use transfers:</p>
                    <ul>
                        <li>Rebalancing workloads across a multi-server fleet.</li>
                        <li>Moving a service off a primary/control-plane node to a dedicated worker.</li>
                        <li>Migrating from one Grid host to another (full server transfer).</li>
                        <li>Repatriating a service that was previously running on a remote node.</li>
                    </ul>

                    <div className="bg-violet-50 dark:bg-violet-900/20 border border-violet-200 dark:border-violet-700 rounded-xl p-4 my-6 not-prose">
                        <p className="text-sm text-violet-800 dark:text-violet-200">
                            <strong>Before you start:</strong> Connect the target server under <strong>Servers → Connect Existing</strong> with its IP/domain and SSH credentials. Only workload-enabled servers (<code>allow_user_workloads=True</code>, <code>is_primary=False</code>) appear as transfer targets in the UI.
                        </p>
                    </div>

                    {/* Transfer Types */}
                    <h2 id="transfer-types" className="text-2xl font-bold flex items-center gap-2">
                        <GitBranch className="w-5 h-5 text-violet-600" /> Transfer Types
                    </h2>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Type</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Scope</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Use when</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">SERVICE</td>
                                    <td className="p-3">One service (and its addons, by association)</td>
                                    <td className="p-3">Moving a single workload between two nodes. Addons follow their parent service automatically.</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-violet-600 dark:text-violet-400">FULL</td>
                                    <td className="p-3">Entire platform (database, all services, configuration)</td>
                                    <td className="p-3">Migrating a complete Grid instance. The target is reinstalled with <code>install.sh</code> and the platform database is restored.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <p>Choose <code>SERVICE</code> for the common case. Use <code>FULL</code> only when relocating the entire platform — not individual workloads.</p>

                    {/* Prerequisites */}
                    <h2 id="prerequisites" className="text-2xl font-bold flex items-center gap-2">
                        <ListChecks className="w-5 h-5 text-violet-600" /> Prerequisites
                    </h2>
                    <ul>
                        <li><strong>Target server is registered and ONLINE.</strong> Connected under <strong>Servers</strong> with its public IP/domain and SSH credentials.</li>
                        <li><strong>SSH credentials are available.</strong> Either stored on the connected target server, or supplied inline. Both SSH keys (PEM-encoded private key) and passwords are supported; password takes precedence if both are present.</li>
                        <li><strong>Target is reachable on TCP/22.</strong> Bidirectional reachability is recommended so the target can confirm connectivity back to the source.</li>
                        <li><strong>Target has a working Grid backend</strong> (for <code>SERVICE</code> transfers). The transfer engine starts it if it is down.</li>
                        <li><strong>Domain is configured on the source</strong> (for automatic DNS cutover). Requires <code>PlatformConfig.cloudflare_api_token</code> and <code>PlatformConfig.domain</code>.</li>
                        <li><strong>Encryption key is set</strong> on the source (<code>BACKUP_ENCRYPTION_KEY</code>) if any of its backups are encrypted.</li>
                    </ul>

                    {/* How to Use */}
                    <h2 id="how-to-use" className="text-2xl font-bold flex items-center gap-2">
                        <ArrowLeftRight className="w-5 h-5 text-violet-600" /> How to Use
                    </h2>

                    <h3>Drag-and-drop (Transfers page)</h3>
                    <ol>
                        <li>Open <strong>Transfers</strong> in the sidebar. Connected workload-enabled servers appear as columns; the local primary node appears on the left.</li>
                        <li>Optional: enter a <strong>New domain</strong> in the top bar. This sets <code>target_public_domain</code> for cross-platform migration (the service&apos;s <code>public_domain</code> is rewritten to <code>&lt;subdomain&gt;.&lt;target_domain&gt;</code> after the transfer completes).</li>
                        <li>Drag a service or addon from one column and drop it onto the target column. Addons are moved by moving their parent service.</li>
                        <li>The UI optimistically updates immediately and POSTs the transfer. The transfer enters the pipeline and begins progressing through its stages.</li>
                        <li>Watch the <strong>Active Stream</strong> panel on the right. Each in-progress transfer shows a progress bar, current step, and the live status. The list polls every 5 seconds.</li>
                        <li>When the status reaches <code>COMPLETED</code>, the service is live on the target. A <strong>Rollback</strong> button is available for 48 hours.</li>
                        <li>To abort a transfer that has not yet completed, click <strong>Cancel</strong>. The transfer moves to <code>CANCELLED</code> and the source workload is left untouched.</li>
                    </ol>

                    <h3>API (scriptable)</h3>
                    <p>The minimal flow:</p>
                    <ol>
                        <li>Resolve the target <code>target_server_id</code> (UUID of the connected <code>ManagedServer</code>) and the source <code>service_id</code> (UUID of the <code>Service</code> record).</li>
                        <li>POST the transfer request to <code>/api/v1/transfers/</code> with <code>transfer_type</code>, <code>service_id</code>, <code>source_server_id</code>, and <code>target_server_id</code>.</li>
                        <li>Poll for status. <code>GET /api/v1/transfers/&#123;id&#125;/</code> returns <code>status</code>, <code>progress_percent</code>, <code>current_step</code>, and live <code>logs</code>.</li>
                        <li>Decide follow-up. When status is <code>COMPLETED</code>, optionally POST <code>/api/v1/transfers/&#123;id&#125;/rollback/</code> to revert. When status is <code>FAILED</code> mid-pipeline, the source workload remains in place.</li>
                    </ol>

                    <h3>Create a transfer</h3>
                    <CodeBlock>{`curl -sS http://localhost:8000/api/v1/transfers/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "transfer_type": "SERVICE",
    "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "target_server_id": "7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e"
  }'`}</CodeBlock>

                    <h3>Roll back a completed transfer</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/transfers/1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9/rollback/ \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>

                    <h3>Cancel an in-progress transfer</h3>
                    <CodeBlock>{`curl -sS -X POST \\
  http://localhost:8000/api/v1/transfers/1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9/cancel/ \\
  -H "Authorization: Token $SMSLY_TOKEN"`}</CodeBlock>

                    <div className="not-prose mt-4 p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-start gap-3">
                        <Code2 className="w-5 h-5 text-violet-600 dark:text-violet-400 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">Full API reference</p>
                            <p className="text-sm text-slate-600 dark:text-slate-400">
                                See <Link href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/docs/transfers.md" className="text-violet-600 dark:text-violet-400 hover:underline font-medium">docs/transfers.md</Link> in the repository for every endpoint, request body, response field, and error code — including the internal <code>register-incoming/</code> node-to-node sync endpoint.
                            </p>
                        </div>
                    </div>

                    {/* Status Reference */}
                    <h2 id="status-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Server className="w-5 h-5 text-violet-600" /> Status Reference
                    </h2>
                    <p>A transfer transitions through the following pipeline. Each stage persists a <code>progress_percent</code> and a <code>current_step</code> so the UI can render a live progress bar without polling logs.</p>
                    <div className="bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl p-5 my-4 not-prose">
                        <pre className="text-xs text-slate-700 dark:text-slate-300 font-mono leading-relaxed">{`PREPARING  →  UPLOADING  →  RESTORING  →  DNS_CUTOVER  →  VERIFYING  →  COMPLETED
                                                                       │
                                                                       ├── ROLLED_BACK  (manual revert)
                                                                       └── FAILED       (any stage can short-circuit here)`}</pre>
                    </div>

                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Status</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">What happens</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Terminal?</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium">PREPARING</td>
                                    <td className="p-3">Source backup is created. On the target, Docker is verified and the Grid backend is started if needed.</td>
                                    <td className="p-3 text-slate-500">No</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">UPLOADING</td>
                                    <td className="p-3">Backup is shipped to the target over SSH. For <code>FULL</code>, <code>install.sh</code> and <code>.env</code> are also uploaded.</td>
                                    <td className="p-3 text-slate-500">No</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">RESTORING</td>
                                    <td className="p-3">Target unpacks the backup, hydrates the database row, loads the Docker image, restores volumes, and starts the container.</td>
                                    <td className="p-3 text-slate-500">No</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">DNS_CUTOVER</td>
                                    <td className="p-3">Cloudflare A records are updated for <code>FULL</code> (apex + wildcard) or, for <code>SERVICE</code> on a Lite Agent target, a per-service A record is created.</td>
                                    <td className="p-3 text-slate-500">No</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium">VERIFYING</td>
                                    <td className="p-3">Health checks run on the target. WireGuard mesh is interconnected so source and target can communicate post-cutover.</td>
                                    <td className="p-3 text-slate-500">No</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-emerald-600 dark:text-emerald-400">COMPLETED</td>
                                    <td className="p-3">Transfer has finished. Service is reassigned to the target, the source container is stopped, and <code>rollback_deadline</code> is set to <code>completed_at + 48h</code>.</td>
                                    <td className="p-3 font-semibold text-emerald-600 dark:text-emerald-400">Yes</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-red-600 dark:text-red-400">FAILED</td>
                                    <td className="p-3">A stage errored. The source workload remains on the source node. <code>error_message</code> is set to a redacted, human-readable summary.</td>
                                    <td className="p-3 font-semibold text-red-600 dark:text-red-400">Yes</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-amber-600 dark:text-amber-400">ROLLED_BACK</td>
                                    <td className="p-3">A successful transfer was reverted. The service is reassigned back to the source and DNS is restored.</td>
                                    <td className="p-3 font-semibold text-amber-600 dark:text-amber-400">Yes</td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-slate-600 dark:text-slate-400">CANCELLED</td>
                                    <td className="p-3">A user cancelled an in-progress transfer. The source workload remains on the source node.</td>
                                    <td className="p-3 font-semibold text-slate-600 dark:text-slate-400">Yes</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <p>
                        <code>PREPARING</code>, <code>UPLOADING</code>, <code>RESTORING</code>, <code>DNS_CUTOVER</code>, and <code>VERIFYING</code> are the <strong>active</strong> statuses. Only one active transfer can exist for a given <code>(owner, target_ip, transfer_type[, service])</code> tuple — creating a second one returns HTTP 409.
                    </p>

                    {/* Security */}
                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-violet-600" /> Security
                    </h2>
                    <p>Transfers handle SSH credentials and the ability to execute commands on remote hosts. The pipeline is hardened at three layers.</p>

                    <h3>SSRF Protection</h3>
                    <p>Public transfer requests validate the resolved target IP. Loopback, link-local, multicast, reserved, and unspecified ranges are always rejected. Private ranges (RFC 1918) are accepted only when the target is a known <code>ManagedServer</code> — this prevents an unauthenticated caller from coercing the backend into opening SSH connections to internal infrastructure.</p>

                    <h3>HMAC Node-to-Node Auth</h3>
                    <p>The <code>POST /api/v1/transfers/register-incoming/</code> endpoint never accepts session or token credentials. It requires:</p>
                    <ul>
                        <li><code>X-SMSLY-Remote-Sync: 1</code> — declares the request as a node-to-node sync.</li>
                        <li><code>X-Request-Timestamp</code> — UNIX seconds, must be within 300 seconds of now.</li>
                        <li><code>X-Gateway-Signature-V2</code> — HMAC-SHA256 over <code>METHOD|path|ts|sha256(body)</code> using the source <code>ManagedServer.gateway_secret</code> (or the platform <code>GATEWAY_SECRET</code> as a fallback). Comparison uses constant-time <code>hmac.compare_digest</code>.</li>
                    </ul>
                    <p>The source IP must resolve to a <code>ManagedServer</code> row that already exists in the target&apos;s database; otherwise the request is rejected with 401.</p>

                    <h3>Encrypted Credential Storage</h3>
                    <p>SSH keys and passwords are stored on the transfer record using <code>EncryptedTextField</code> / <code>EncryptedCharField</code> (Fernet) — values are encrypted at rest in the database.</p>
                    <p>The transfer worker scrubs these fields as soon as the transfer reaches a terminal state:</p>
                    <ul>
                        <li><code>target_ssh_key</code> and <code>target_ssh_password</code> are cleared on <code>COMPLETED</code>, <code>FAILED</code>, and <code>ROLLED_BACK</code>.</li>
                        <li><code>source_ssh_key</code> and <code>source_ssh_password</code> are cleared on <code>FAILED</code>.</li>
                        <li>When the Celery worker fails to enqueue the transfer, all four fields are cleared on the <code>FAILED</code> record.</li>
                    </ul>
                    <p>Transfer logs are also redacted before persistence: PEM private key blocks, <code>*_TOKEN</code>/<code>*_SECRET</code>/<code>*_PASSWORD</code>/<code>*_KEY</code> assignments, and <code>user:password@</code> segments in URLs are stripped.</p>

                    {/* Troubleshooting */}
                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-violet-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;Target server IP is in a forbidden range (SSRF protection)&quot;</h3>
                    <p>The resolved target IP is in a loopback, link-local, or RFC 1918 range, and you did not select a <code>ManagedServer</code> for it. Use a connected <code>ManagedServer</code> (<code>target_server_id</code>) when transferring to a private LAN address, or supply a public IP.</p>

                    <h3>&quot;No SSH credentials available for target server&quot;</h3>
                    <p>Neither <code>target_ssh_key</code> nor <code>target_ssh_password</code> was supplied, and the <code>ManagedServer</code> for the target has no stored credentials. Open <strong>Servers → Edit</strong> on the target and re-save the SSH key or password, or pass credentials in the API request body.</p>

                    <h3>&quot;Target server &apos;X&apos; is currently OFFLINE. Transfers are only allowed to ONLINE nodes.&quot;</h3>
                    <p>The connected server is registered but not currently online. Bring the target back online, wait for the next mesh probe to mark it <code>ONLINE</code>, then re-queue the transfer.</p>

                    <h3>&quot;Source SSH credentials required for node-to-node transfer.&quot;</h3>
                    <p>The source is a connected (non-local) <code>ManagedServer</code> with no stored SSH credentials. Either pass <code>source_ssh_key</code> / <code>source_ssh_password</code> in the request, or edit the source server and save its SSH credentials.</p>

                    <h3>&quot;Encrypted backup detected but BACKUP_ENCRYPTION_KEY is not set.&quot;</h3>
                    <p>The source&apos;s backup is encrypted, but the controller&apos;s environment does not have the matching key. Set <code>BACKUP_ENCRYPTION_KEY</code> in the source <code>.env</code> to the same value used at backup time, restart the backend, and re-create the transfer.</p>

                    <h3>Transfer hangs in <code>RESTORING</code></h3>
                    <p>The remote Django restore script is waiting on the database. Inside the target backend container:</p>
                    <CodeBlock lang="bash">{`docker exec -it smsly-hosting-backend-1 python manage.py shell \\
  -c "from django.db import connection; connection.ensure_connection()"`}</CodeBlock>
                    <p>If the connection fails, the target&apos;s PostgreSQL is unreachable. Restart the database with <code>docker compose -f docker-compose.prod.yml restart db</code> on the target and let the transfer retry.</p>

                    <h3>&quot;RESTORE_FAILED: …&quot; in transfer logs</h3>
                    <p>The remote restore script reported an unrecoverable error. The full traceback is in the transfer&apos;s <code>logs</code> field. The most common cause is a mismatched owner email on the target — the script falls back to a superuser but logs a warning. Verify the source&apos;s service owner has a corresponding account on the target.</p>

                    <h3>Rollback button is missing</h3>
                    <p><code>can_rollback</code> is <code>False</code> because either (a) the transfer did not complete, (b) the 48-hour rollback window has passed, or (c) rollback was already used. After the deadline the source state is no longer guaranteed to be intact and a rollback could corrupt the source.</p>

                    <h3>Service is live on target but DNS still points to source</h3>
                    <p>Cloudflare DNS is only updated automatically when <code>PlatformConfig.cloudflare_api_token</code> and <code>PlatformConfig.domain</code> are set on the source. If either is missing, update the A record manually (for <code>SERVICE</code> on a Lite Agent target, point the service subdomain at the target; for <code>FULL</code>, point the apex + wildcard at the target).</p>

                    {/* Navigation */}
                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> All Docs
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
