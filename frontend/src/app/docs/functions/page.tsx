'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Code, BookOpen, Shield, Code2, Copy, Check, Wrench, ListChecks, Lock, Layers } from 'lucide-react';

const tocItems = [
    { id: 'overview', label: 'Overview', icon: BookOpen },
    { id: 'reality-check', label: 'Reality Check', icon: Layers },
    { id: 'runtimes', label: 'Runtimes', icon: Code },
    { id: 'hardening', label: 'Hardening', icon: Shield },
    { id: 'limits', label: 'Limits', icon: ListChecks },
    { id: 'triggers', label: 'Triggers', icon: Code2 },
    { id: 'api-reference', label: 'API Reference', icon: Code2 },
    { id: 'security', label: 'Security', icon: Lock },
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

export default function FunctionsDocsPage() {
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

            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-orange-50/60 to-white dark:from-orange-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-orange-600 dark:text-orange-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-orange-100 dark:bg-orange-900/50 rounded-xl">
                            <Code className="w-5 h-5 text-orange-700 dark:text-orange-300" />
                        </div>
                        <span className="text-sm font-semibold text-orange-600 dark:text-orange-400 uppercase tracking-wider">Serverless Functions</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Serverless Functions
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg md:text-xl max-w-2xl leading-relaxed">
                        Inline source code in Node 18 or Python 3.9. A thin HTTP shim on a hardened container, with an SSRF guard on every outbound call.
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
                                            ? 'bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 font-semibold'
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
                        <BookOpen className="w-5 h-5 text-orange-600" /> Overview
                    </h2>
                    <p>Grid&apos;s &quot;serverless&quot; surface is a thin serverless-style runtime on top of the standard service pipeline. Every function is a real Docker container built from a hand-rolled HTTP wrapper, hardened for untrusted code, and constrained to the public network by an SSRF guard. Functions flow through the same build / deploy / health-check pipeline as any other service — there is no separate FaaS substrate.</p>

                    <h2 id="reality-check" className="text-2xl font-bold flex items-center gap-2">
                        <Layers className="w-5 h-5 text-orange-600" /> Reality Check: Not a True FaaS
                    </h2>
                    <p>Grid functions are <strong>not</strong> AWS Lambda. They are full Docker containers wrapped in a small HTTP shim. Concretely:</p>
                    <ul>
                        <li>A function is a <code>Service</code> row with <code>deploy_type=&apos;FUNCTION&apos;</code>, <code>function_code</code>, <code>function_runtime</code>, and <code>function_handler</code> set.</li>
                        <li>The build phase emits a Dockerfile from a static template (<code>Node 18</code> or <code>Python 3.9</code>), drops the user code into <code>/app/</code>, and produces a <code>smsly/function-&lt;id&gt;</code> image.</li>
                        <li>The container listens on port 8000. The HTTP shim parses the incoming request, runs the user code, captures the response, and returns it. There is no V8 isolate, no micro-VM, no warm pool.</li>
                        <li>Cold start = container startup time. The first request to a brand-new function pays a 200–2000 ms container boot cost; subsequent requests on the same container are microseconds.</li>
                        <li>Functions are bounded by the same <code>Service</code> resource fields (<code>memory_mb</code>, <code>cpu_shares</code>, <code>min_replicas</code>, <code>max_replicas</code>).</li>
                        <li>Triggers are HTTP only. There is no cron, no queue, no event source.</li>
                    </ul>
                    <p>Treat the feature as &quot;inline code with a streamlined UI&quot;, not as a competitor to dedicated FaaS runtimes. If you need bursty scale-to-zero and sub-100 ms cold starts, run a regular service with <code>min_replicas=0</code> and let the autoscaler handle it.</p>

                    <h2 id="runtimes" className="text-2xl font-bold flex items-center gap-2">
                        <Code className="w-5 h-5 text-orange-600" /> Runtimes
                    </h2>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Runtime</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Image</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Wrapper</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr>
                                    <td className="p-3 font-mono font-medium text-orange-600 dark:text-orange-400">node18</td>
                                    <td className="p-3">node:18-alpine</td>
                                    <td className="p-3">/app/smsly-function-runner.cjs invokes <code>handler(event, context)</code></td>
                                </tr>
                                <tr>
                                    <td className="p-3 font-mono font-medium text-orange-600 dark:text-orange-400">python3.9</td>
                                    <td className="p-3">python:3.9-slim</td>
                                    <td className="p-3">/app/smsly_function_runner.py invokes <code>handler(event, context)</code></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <p>The wrappers are intentionally minimal: no built-in HTTP client, no third-party packages, no env var templating. Anything the function needs must be present in the code itself (or installed at build time). The platform&apos;s outbound <code>fetch</code> / <code>urllib</code> / <code>requests</code> / <code>http.client</code> calls are monkey-patched at startup to enforce the SSRF guard.</p>

                    <h2 id="hardening" className="text-2xl font-bold flex items-center gap-2">
                        <Shield className="w-5 h-5 text-orange-600" /> Hardening
                    </h2>

                    <h3>Non-Root User</h3>
                    <p>The function Dockerfile emits:</p>
                    <CodeBlock lang="dockerfile">{`RUN addgroup -S function_user && adduser -S function_user -G function_user
USER function_user`}</CodeBlock>
                    <p>This means the user code runs as UID 1000-ish, not as root. A code-execution vulnerability inside the function cannot <code>mount</code>, <code>iptables</code>, or write to <code>/proc/sys</code>. It also cannot bind to port 80 (the wrapper is hard-coded to 8000). The <code>node18</code> image additionally uses <code>node</code> (UID 1000) as the runtime user.</p>

                    <h3>SSRF Guard (Outbound Network Policy)</h3>
                    <p>The function runner is sandboxed against outbound network calls to internal infrastructure. The guard runs at runtime, in the same process, by monkey-patching the standard library HTTP client. It applies to:</p>
                    <ul>
                        <li><strong>Node.js</strong> — <code>fetch</code>, <code>http.request</code>, <code>https.request</code>, and the global <code>http</code> / <code>https</code> modules.</li>
                        <li><strong>Python</strong> — <code>urllib.request</code>, <code>http.client</code>, and the <code>requests</code> library (when imported).</li>
                    </ul>
                    <p>The blocked ranges are:</p>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Range</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Reason</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3 font-mono">127.0.0.0/8</td><td className="p-3">Loopback (Docker socket, metadata)</td></tr>
                                <tr><td className="p-3 font-mono">10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16</td><td className="p-3">RFC 1918 private</td></tr>
                                <tr><td className="p-3 font-mono">169.254.0.0/16</td><td className="p-3">Link-local / cloud metadata</td></tr>
                                <tr><td className="p-3 font-mono">100.64.0.0/10</td><td className="p-3">Carrier-grade NAT (RFC 6598)</td></tr>
                                <tr><td className="p-3 font-mono">0.0.0.0/8, 255.255.255.255/32</td><td className="p-3">Unspecified / broadcast</td></tr>
                                <tr><td className="p-3 font-mono">224.0.0.0/4</td><td className="p-3">Multicast</td></tr>
                                <tr><td className="p-3 font-mono">fc00::/7</td><td className="p-3">IPv6 unique-local (ULA)</td></tr>
                                <tr><td className="p-3 font-mono">fe80::/10</td><td className="p-3">IPv6 link-local</td></tr>
                                <tr><td className="p-3 font-mono">::1/128</td><td className="p-3">IPv6 loopback</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>The check runs against the <strong>resolved IP</strong>, not the hostname. A function that calls <code>http://10-0-0-1.xip.io/</code> is still blocked (the resolver maps that hostname to <code>10.0.0.1</code>). The check is DNS-rebinding-aware: the resolver result is captured at request time and compared against the guard, not deferred to a separate background lookup.</p>
                    <p>The guard returns HTTP 452 (a non-standard &quot;blocked by policy&quot; code) for any outbound request that fails the check. The platform&apos;s <code>AuditLog</code> is <strong>not</strong> written for these — the function simply sees a thrown error.</p>

                    <h3>DNS Rebinding Mitigation</h3>
                    <p>A naive SSRF guard that only checks the URL is bypassable via DNS rebinding: the attacker controls a DNS record that initially resolves to a public IP, then flips to <code>10.0.0.1</code> after the guard&apos;s check. Grid&apos;s guard is <strong>resolve-then-check</strong> (not <strong>check-then-resolve</strong>) and re-checks at connect time. The patched <code>urllib.request</code> and <code>http.client</code> call <code>socket.getaddrinfo()</code> first, validate every IP in the result against the blocklist, and refuse the request if any IP matches.</p>

                    <h3>Container Security Directives</h3>
                    <p>The generated Dockerfile includes:</p>
                    <ul>
                        <li><code>USER function_user</code> / <code>node</code> — non-root execution.</li>
                        <li><code>EXPOSE 8000</code> — single-port surface; no host networking.</li>
                        <li><code>HEALTHCHECK curl -fsS http://127.0.0.1:8000/health</code> every 30s — detects wedged wrappers.</li>
                        <li><code>WORKDIR /app</code> — read-only by default.</li>
                    </ul>

                    <h2 id="limits" className="text-2xl font-bold flex items-center gap-2">
                        <ListChecks className="w-5 h-5 text-orange-600" /> Limits
                    </h2>
                    <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Limit</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Default</th>
                                    <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Source</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td className="p-3">Code size</td><td className="p-3 font-mono">256 KB</td><td className="p-3"><code>MAX_FUNCTION_CODE_BYTES</code></td></tr>
                                <tr><td className="p-3">Request body size</td><td className="p-3 font-mono">1 MB</td><td className="p-3"><code>MAX_FUNCTION_BODY_BYTES</code></td></tr>
                                <tr><td className="p-3">Execution time</td><td className="p-3 font-mono">30 s</td><td className="p-3"><code>FUNCTION_TIMEOUT_SECONDS</code></td></tr>
                                <tr><td className="p-3">Memory</td><td className="p-3">inherits <code>Service.memory_mb</code></td><td className="p-3">same as a regular service</td></tr>
                                <tr><td className="p-3">Concurrency</td><td className="p-3">inherits <code>min_replicas</code> / <code>max_replicas</code></td><td className="p-3">one request per container</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <p>A function that exceeds the execution timeout returns HTTP 504 (<code>Gateway Timeout</code>) and the container is recycled. A function that exceeds the body limit returns HTTP 413 (<code>Payload Too Large</code>) without invoking the user code.</p>

                    <h2 id="triggers" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-orange-600" /> Triggers
                    </h2>
                    <p>The only trigger is HTTP. There is <strong>no</strong> built-in cron, <strong>no</strong> queue subscriber, <strong>no</strong> event source. The endpoint URL is:</p>
                    <CodeBlock>{`https://<service.public_domain>/fn/<function_name>`}</CodeBlock>
                    <p>For example, a service with <code>function_name=&apos;hello&apos;</code> exposes:</p>
                    <CodeBlock>{`POST https://hello.example.com/fn/hello`}</CodeBlock>
                    <p>The HTTP method on the request becomes the HTTP method on the wrapper. The body is JSON-decoded and passed as <code>event.body</code>; query string is <code>event.queryStringParameters</code>; headers are <code>event.headers</code> (with <code>Host</code> and <code>Content-Length</code> removed for size). The function&apos;s return value is JSON-serialized with status code 200 by default; the user can override by returning <code>&#123;statusCode, headers, body&#125;</code>.</p>
                    <p>If you need scheduled invocation, point an external cron (GitHub Actions, system cron, Cloudflare Workers cron) at the function URL with an empty POST body. The platform does not provide a built-in scheduler for functions.</p>

                    <h2 id="api-reference" className="text-2xl font-bold flex items-center gap-2">
                        <Code2 className="w-5 h-5 text-orange-600" /> API Reference
                    </h2>
                    <p>Function endpoints live under <code>/api/v1/services/</code>. Functions are created, updated, and deployed via the same endpoints as a regular service, with <code>deploy_type=&apos;FUNCTION&apos;</code>.</p>

                    <h3>Create a function</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/services/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "hello",
    "deploy_type": "FUNCTION",
    "function_runtime": "node18",
    "function_code": "module.exports.handler = async (event) => ({ statusCode: 200, body: \"hello, \" + (event.queryStringParameters?.name || \"world\") });",
    "public_domain": "hello.example.com"
  }'`}</CodeBlock>
                    <p>Returns HTTP 201 with the new service record and a triggered deployment.</p>

                    <h3>Update a function</h3>
                    <CodeBlock>{`curl -sS -X PATCH http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"function_code": "module.exports.handler = async () => ({ statusCode: 200, body: \"v2\" });"}'`}</CodeBlock>
                    <p>The patch auto-triggers a fresh deployment.</p>

                    <h3>Synchronous invocation (test)</h3>
                    <CodeBlock>{`curl -sS -X POST http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/invoke/ \\
  -H "Authorization: Token $SMSLY_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"event": {"queryStringParameters": {"name": "alice"}}}'`}</CodeBlock>
                    <p>Runs the function on the controller, not on the deployed container. Useful for testing, cron-style invocation, or admin operations. Throttled to 60 calls/minute per user (<code>FunctionInvokeRateThrottle</code>).</p>

                    <div className="not-prose mt-4 p-4 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-start gap-3">
                        <Code2 className="w-5 h-5 text-orange-600 dark:text-orange-400 flex-shrink-0 mt-0.5" />
                        <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">Full API reference</p>
                            <p className="text-sm text-slate-600 dark:text-slate-400">
                                See <Link href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/docs/functions.md" className="text-orange-600 dark:text-orange-400 hover:underline font-medium">docs/functions.md</Link> in the repository for the complete request body, response field schema, error codes, and the custom-dependency vendoring pattern.
                            </p>
                        </div>
                    </div>

                    <h2 id="security" className="text-2xl font-bold flex items-center gap-2">
                        <Lock className="w-5 h-5 text-orange-600" /> Security
                    </h2>

                    <h3>Outbound Network Policy</h3>
                    <p>The SSRF guard described above is the only egress control. There is no separate firewall. Operators who want a stronger guarantee should add a network policy at the cluster level (Calico, Cilium) and refuse to run Grid on a network where the SSRF guard&apos;s blocklist is insufficient.</p>
                    <p>The blocklist is <strong>not</strong> configurable. If you need to allow <code>10.0.0.0/8</code> (e.g. internal services), the function surface is not the right tool — deploy a regular service and use a private add-on instead.</p>

                    <h3>Encrypted Code at Rest</h3>
                    <p><code>Service.function_code</code> is stored as a regular <code>TextField</code> (not encrypted). The reasoning: the code is runnable, so the operator can read it. There is no secret material in the function code by policy. If the code includes secrets (which it should not), use a regular service with <code>EnvironmentVariable</code> and <code>is_secret=True</code> instead.</p>

                    <h3>API Key Management</h3>
                    <p>Functions inherit <code>Service.env_vars</code> (with the same <code>is_secret</code> masking and Fernet encryption). The standard precedence rules apply — see <Link href="/docs/deployments" className="text-orange-600 dark:text-orange-400 hover:underline">Deployments</Link>.</p>

                    <h3>No Cross-Tenant Data</h3>
                    <p>The function runs in its own container with no shared filesystem. It cannot read other services&apos; volumes, addons, or backups. The platform&apos;s database connection is also inaccessible.</p>

                    <h2 id="troubleshooting" className="text-2xl font-bold flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-orange-600" /> Troubleshooting
                    </h2>

                    <h3>&quot;Function code exceeds 256 KB&quot;</h3>
                    <p>The <code>function_code</code> field is capped at 256 KB. Move large assets out of the function (use a static service, or fetch them at runtime from a CDN).</p>

                    <h3>&quot;SSRF guard blocked outbound request to 10.x.x.x&quot;</h3>
                    <p>The function tried to call an internal IP. The guard is intentional and not configurable. If you need to call internal services, deploy a regular service and put it on the same Docker network as the target.</p>

                    <h3>&quot;Execution timed out after 30s&quot;</h3>
                    <p>Raise <code>FUNCTION_TIMEOUT_SECONDS</code> in the platform <code>.env</code> (max 300), or refactor the function to return early. Long-running tasks belong in a worker service, not a function.</p>

                    <h3>&quot;Function runs in <code>invoke/</code> but returns 504 in production&quot;</h3>
                    <p>The deployed container is OOM-killed or CPU-throttled. Check <code>Service.memory_mb</code> and <code>cpu_shares</code>. The function&apos;s <code>HEALTHCHECK</code> will also have flipped to <code>unhealthy</code> — check the deployment&apos;s health-check phase.</p>

                    <h3>&quot;Health check returns 200 but the function returns 502&quot;</h3>
                    <p>The wrapper&apos;s <code>/health</code> endpoint does not invoke the user code. A 200 on <code>/health</code> only means the wrapper is alive. Test the function with <code>POST /api/v1/services/&#123;id&#125;/invoke/</code> to see the actual error.</p>

                    <h3>&quot;Function works on one replica but not another&quot;</h3>
                    <p><code>Service.min_replicas &gt; 1</code> and a stale container is serving the request. Roll the deployment, or set <code>min_replicas=1</code> and let the autoscaler scale up.</p>

                    <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                        <Link href="/docs/deployments" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                            <ArrowLeft size={14} /> Deployments
                        </Link>
                        <Link href="/docs/autoscaling" className="flex items-center gap-1.5 text-sm text-orange-600 dark:text-orange-400 hover:underline font-medium">
                            Autoscaling <ArrowRight size={14} />
                        </Link>
                    </div>

                </article>
            </div>
        </main>
    );
}
