import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Code2 } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';

export const metadata: Metadata = {
  title: 'REST API Reference',
  description:
    'Automate services, deployments, addons, servers, and health probes using the Trulay Grid v1 REST API.',
  alternates: { canonical: '/docs/api' },
};

const coreEndpoints = [
  { method: 'GET', path: '/api/v1/system/health/', auth: 'Public', desc: 'Database, cache/broker, uptime, and platform readiness probe' },
  { method: 'GET / POST', path: '/api/v1/projects/', auth: 'Token / Session', desc: 'List or create isolated project workspaces' },
  { method: 'GET / POST', path: '/api/v1/services/', auth: 'Token / Session', desc: 'Manage deployed services, ports, env vars, and domains' },
  { method: 'POST', path: '/api/v1/services/{id}/deploy/', auth: 'Token / Session', desc: 'Queue a new async build/scan/deploy pipeline run' },
  { method: 'GET', path: '/api/v1/deployments/', auth: 'Token / Session', desc: 'Inspect deployment state, build logs, and runtime logs' },
  { method: 'GET / POST', path: '/api/v1/addons/', auth: 'Token / Session', desc: 'Provision and manage PostgreSQL, Redis, MongoDB, Qdrant, and storage buckets' },
  { method: 'GET / POST', path: '/api/v1/servers/', auth: 'Staff / Admin', desc: 'Register remote nodes, WireGuard mesh peers, and capacity checks' },
];

export default function ApiRefPage() {
  return (
    <div className="bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-orange-50/70 to-white dark:from-orange-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 min-h-[44px] text-sm text-orange-700 dark:text-orange-400 hover:underline mb-4">
            <ArrowLeft size={14} aria-hidden="true" /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-orange-100 dark:bg-orange-900/50 rounded-xl">
              <Code2 className="w-5 h-5 text-orange-700 dark:text-orange-300" aria-hidden="true" />
            </div>
            <span className="text-sm font-semibold text-orange-700 dark:text-orange-400 uppercase tracking-wider">Reference</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
            REST API Reference
          </h1>
          <p className="text-slate-600 dark:text-slate-300 text-lg max-w-2xl leading-relaxed">
            Automate infrastructure provisioning, deployments, and observability via the Grid v1 REST API.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <h2>Base URL &amp; Authentication</h2>
        <p>
          All API routes are mounted under <code>https://&lt;your-grid-host&gt;/api/v1</code>. Dashboard sessions use an <code>HttpOnly</code> cookie, while automation scripts and the{' '}
          <Link href="/docs/cli">smsly CLI</Link> pass a token generated under <strong>Settings → API Keys</strong>:
        </p>
        <CodeBlock>{`curl -sS https://your-grid-host/api/v1/services/ \\
  -H "Authorization: Token smsly_your_token_here"`}</CodeBlock>

        <h2>Core Resource Endpoints</h2>
        <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 my-6">
          <table className="w-full text-sm text-left border-collapse">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                <th scope="col" className="p-3 font-bold text-slate-700 dark:text-slate-300 uppercase text-xs">Method</th>
                <th scope="col" className="p-3 font-bold text-slate-700 dark:text-slate-300 uppercase text-xs">Endpoint</th>
                <th scope="col" className="p-3 font-bold text-slate-700 dark:text-slate-300 uppercase text-xs">Auth</th>
                <th scope="col" className="p-3 font-bold text-slate-700 dark:text-slate-300 uppercase text-xs">Purpose</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {coreEndpoints.map((ep) => (
                <tr key={ep.path}>
                  <td className="p-3 font-mono text-xs font-semibold text-emerald-700 dark:text-emerald-400 whitespace-nowrap">{ep.method}</td>
                  <td className="p-3 font-mono text-xs text-slate-900 dark:text-slate-100 whitespace-nowrap">{ep.path}</td>
                  <td className="p-3 text-xs text-slate-600 dark:text-slate-300 whitespace-nowrap">{ep.auth}</td>
                  <td className="p-3 text-xs text-slate-700 dark:text-slate-300">{ep.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h2>OpenAPI Schema &amp; Local Swagger</h2>
        <p>
          In production, live schema introspection is disabled to prevent unauthenticated API enumeration. On staging or local environments with{' '}
          <code>DEBUG=True</code>, interactive OpenAPI documentation is available directly at:
        </p>
        <ul>
          <li><code>/api/docs/swagger/</code> &mdash; Swagger UI</li>
          <li><code>/api/docs/redoc/</code> &mdash; ReDoc UI</li>
          <li><code>/api/docs/schema/</code> &mdash; Raw OpenAPI 3 JSON/YAML spec</li>
        </ul>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs/cli" className="inline-flex items-center gap-1.5 min-h-[44px] text-sm text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white">
            <ArrowLeft size={14} aria-hidden="true" /> CLI Reference
          </Link>
          <Link href="/docs/addons" className="inline-flex items-center gap-1.5 min-h-[44px] text-sm text-emerald-700 dark:text-emerald-400 hover:underline font-medium">
            Addons <ArrowRight size={14} aria-hidden="true" />
          </Link>
        </div>
      </div>
    </div>
  );
}
