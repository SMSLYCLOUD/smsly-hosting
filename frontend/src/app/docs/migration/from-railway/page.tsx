import Link from 'next/link';
import { ArrowLeft, ArrowRight, Layers } from 'lucide-react';

export default function FromRailwayPage() {
    return (
        <main className="min-h-screen bg-white dark:bg-slate-950">
            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-zinc-50 to-white dark:from-zinc-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs/migration" className="inline-flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400 hover:underline mb-4">
                        <ArrowLeft size={14} /> Migration Guides
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-zinc-100 dark:bg-zinc-800 rounded-xl">
                            <Layers className="w-5 h-5 text-black dark:text-white" />
                        </div>
                        <span className="text-sm font-semibold text-zinc-600 dark:text-zinc-400 uppercase tracking-wider">Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Migrating from Railway
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
                        Move your Railway projects to Grid. Same Docker workflow, your own infrastructure, 80%+ cost reduction.
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
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Railway</th>
                                <th className="p-3 text-left font-bold text-emerald-600 uppercase text-xs bg-emerald-50/50 dark:bg-emerald-900/10">Grid</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">Pricing Model</td>
                                <td className="p-3 text-slate-500">Usage-based (RAM/CPU minutes)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Flat rate (you pay your VPS provider)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Storage Cost</td>
                                <td className="p-3 text-slate-500">Volumes in beta, expensive at scale</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Included, any size</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Database HA</td>
                                <td className="p-3 text-slate-500">Single instance (no HA)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Patroni HA with automatic failover</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Multi-Cloud</td>
                                <td className="p-3 text-slate-500">GCP-locked</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Any provider (AWS, GCP, Azure, bare metal)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">AI Diagnostics</td>
                                <td className="p-3 text-slate-500">None</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Built-in (Z-Score + LLM)</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>Migration Steps</h2>

                <h3>1. Export your Railway data</h3>
                <ul>
                    <li>Export Railway Postgres databases via <code>pg_dump</code></li>
                    <li>Download Railway volume snapshots</li>
                    <li>Export Redis data from Railway Redis plugin</li>
                    <li>Copy environment variables from Railway dashboard</li>
                </ul>

                <h3>2. Keep your Dockerfile — no changes needed</h3>
                <p>Railway and Grid both use standard Docker containers. Your existing <code>Dockerfile</code> and <code>railway.json</code> will work on Grid with minimal changes. The Nixpacks builder also auto-detects your language.</p>

                <h3>3. Deploy to Grid</h3>
                <ol>
                    <li>Connect your GitHub repository to Grid</li>
                    <li>Create a new service — your repo will be auto-detected</li>
                    <li>Set environment variables (copy from Railway)</li>
                    <li>Click Deploy</li>
                </ol>

                <h3>4. Migrate your database</h3>
                <pre><code>{`# Export from Railway
pg_dump --no-owner postgresql://railway:...@... > backup.sql

# Import to Grid's managed Postgres
psql postgresql://grid:...@... < backup.sql`}</code></pre>

                <h3>5. Configure addons</h3>
                <p>Grid includes managed addons that replace Railway plugins:</p>
                <ul>
                    <li><strong>PostgreSQL</strong> — included, with Patroni HA replication</li>
                    <li><strong>Redis</strong> — included, for caching and pub/sub</li>
                    <li><strong>Persistent Volumes</strong> — included, any size</li>
                    <li><strong>Custom Domains</strong> — included, with auto-SSL</li>
                </ul>

                <h3>6. Point your domain</h3>
                <p>Update your DNS A record to point to your Grid server IP. Caddy automatically provisions Let&apos;s Encrypt SSL certificates — no manual cert management.</p>

                <h2>What changes in your workflow</h2>
                <ul>
                    <li><code>railway up</code> → <code>git push</code> or Grid CLI</li>
                    <li><code>railway run</code> → Grid&apos;s web dashboard or API</li>
                    <li><code>RAILWAY_*</code> env vars → no longer needed (Grid provides <code>GRID_*</code> equivalents)</li>
                    <li>Same Docker workflow — your existing containers run unmodified</li>
                </ul>

                <h2>What you gain</h2>
                <ul>
                    <li><strong>80%+ cost reduction</strong> at scale — Railway&apos;s usage markup adds up fast</li>
                    <li><strong>Predictable pricing</strong> — flat server cost, not per-minute billing</li>
                    <li><strong>Multi-cloud flexibility</strong> — deploy across providers or migrate freely</li>
                    <li><strong>Database HA</strong> — automatic failover, not a single point of failure</li>
                    <li><strong>Built-in AI diagnostics</strong> — automatic root cause analysis for failures</li>
                </ul>

                {/* Navigation */}
                <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                    <Link href="/docs/migration/from-vercel" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                        <ArrowLeft size={14} /> From Vercel
                    </Link>
                    <Link href="/docs/migration/from-heroku" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
                        Migrate from Heroku <ArrowRight size={14} />
                    </Link>
                </div>
            </div>
        </main>
    );
}
