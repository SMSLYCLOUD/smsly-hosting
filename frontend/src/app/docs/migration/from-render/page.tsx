import Link from 'next/link';
import { ArrowLeft, ArrowRight, ArrowUpRight } from 'lucide-react';

export default function FromRenderPage() {
    return (
        <main className="min-h-screen bg-white dark:bg-slate-950">
            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-cyan-50 to-white dark:from-cyan-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs/migration" className="inline-flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400 hover:underline mb-4">
                        <ArrowLeft size={14} /> Migration Guides
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-cyan-100 dark:bg-cyan-900/50 rounded-xl">
                            <ArrowUpRight className="w-5 h-5 text-cyan-700 dark:text-cyan-300" />
                        </div>
                        <span className="text-sm font-semibold text-cyan-600 dark:text-cyan-400 uppercase tracking-wider">Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Migrating from Render
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
                        Move your Render services to Grid. No sleeping dynos, predictable pricing, and faster builds.
                    </p>
                </div>
            </section>

            <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
                <div className="docs-callout docs-callout-warning not-prose">
                    <p className="!mt-0 text-sm">
                        <strong>Before you start:</strong> Ensure Grid is installed and running. See the{' '}
                        <Link href="/docs/install" className="text-amber-900 dark:text-amber-100 underline font-semibold">Installation Guide</Link> if you haven&apos;t set it up yet.
                    </p>
                </div>

                <h2>Key Differences</h2>
                <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 mb-8">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Capability</th>
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Render</th>
                                <th className="p-3 text-left font-bold text-emerald-600 uppercase text-xs bg-emerald-50/50 dark:bg-emerald-900/10">Grid</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">Sleep on Free Tier</td>
                                <td className="p-3 text-slate-500">Yes (spins down after inactivity)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">No sleeping (always-on)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Build Speed</td>
                                <td className="p-3 text-slate-500">Slow (shared build infrastructure)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Fast (your own server builds locally)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Postgres HA</td>
                                <td className="p-3 text-slate-500">None (single instance unless paid)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Patroni HA with automatic failover</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Docker Emphasis</td>
                                <td className="p-3 text-slate-500">Supported but not primary</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Native (Dockerfile or Nixpacks)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Bandwidth</td>
                                <td className="p-3 text-slate-500">100GB/mo (Starter), then $0.10/GB</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Provider cost (often free up to 20TB)</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>Migration Steps</h2>

                <h3>1. Export your Render data</h3>
                <ul>
                    <li>Export Render Postgres via <code>pg_dump</code></li>
                    <li>Download any persistent disk data</li>
                    <li>Copy environment variables from the Render dashboard</li>
                    <li>Note your custom domain configuration</li>
                </ul>

                <h3>2. Prepare your project</h3>
                <p>Render supports Dockerfiles, and so does Grid. Your existing <code>Dockerfile</code> should work without changes. If you&apos;re using Render&apos;s native build system (no Dockerfile), create one or let Nixpacks auto-detect your framework.</p>
                <pre><code>{`# Example: Node.js
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
EXPOSE $PORT
CMD ["npm", "start"]`}</code></pre>

                <h3>3. Deploy to Grid</h3>
                <ol>
                    <li>Push your repo to GitHub</li>
                    <li>Connect GitHub to Grid</li>
                    <li>Create a new service and select your repo</li>
                    <li>Add your environment variables</li>
                    <li>Deploy — builds run on your own server (faster)</li>
                </ol>

                <h3>4. Migrate your database</h3>
                <pre><code>{`# Export from Render
PGPASSWORD=... pg_dump \\
  -h your-render-db.internal \\
  -U render_user \\
  -d render_db > render_backup.sql

# Import to Grid's managed Postgres
psql postgresql://grid_user:...@grid-db:5432/grid_db < render_backup.sql`}</code></pre>

                <h3>5. Configure your domain</h3>
                <ul>
                    <li>Update your DNS A record to point to your Grid server IP</li>
                    <li>Caddy will automatically provision Let&apos;s Encrypt SSL certificates</li>
                    <li>Remove Render&apos;s DNS configuration</li>
                </ul>

                <h2>Render service equivalents on Grid</h2>
                <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 mb-8">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Render Service</th>
                                <th className="p-3 text-left font-bold text-emerald-600 uppercase text-xs bg-emerald-50/50 dark:bg-emerald-900/10">Grid Equivalent</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">Web Service</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Service (long-running container)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Cron Job</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Celery Beat scheduled tasks</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Background Worker</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Celery worker containers</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Render Postgres</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Managed Postgres (Patroni HA)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Render Redis</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Managed Redis</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Static Site</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Service + static file serving via Caddy</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Preview Environments</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">PR Previews (full-stack with DB)</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>What you gain</h2>
                <ul>
                    <li><strong>No sleeping services</strong> — always-on, even on the free tier</li>
                    <li><strong>Faster builds</strong> — your own server&apos;s CPU, not shared build infrastructure</li>
                    <li><strong>Predictable pricing</strong> — no per-GB bandwidth fees, no per-service tiers</li>
                    <li><strong>Database HA</strong> — Patroni replication instead of a single Postgres instance</li>
                    <li><strong>No bandwidth overage</strong> — most VPS providers include 10-20TB free</li>
                </ul>

                {/* Navigation */}
                <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                    <Link href="/docs/migration/from-heroku" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                        <ArrowLeft size={14} /> From Heroku
                    </Link>
                    <Link href="/docs/migration" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
                        All Migration Guides <ArrowRight size={14} />
                    </Link>
                </div>
            </div>
        </main>
    );
}
