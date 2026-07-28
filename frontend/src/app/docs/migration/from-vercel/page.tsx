import Link from 'next/link';
import { ArrowLeft, ArrowRight, Zap, Check, X } from 'lucide-react';

export default function FromVercelPage() {
    return (
        <main className="min-h-screen bg-white dark:bg-slate-950">
            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-zinc-50 to-white dark:from-zinc-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs/migration" className="inline-flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400 hover:underline mb-4">
                        <ArrowLeft size={14} /> Migration Guides
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-zinc-100 dark:bg-zinc-800 rounded-xl">
                            <Zap className="w-5 h-5 text-black dark:text-white" />
                        </div>
                        <span className="text-sm font-semibold text-zinc-600 dark:text-zinc-400 uppercase tracking-wider">Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Migrating from Vercel
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
                        Move your projects from Vercel&apos;s serverless platform to Grid&apos;s container-based infrastructure.
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
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Vercel</th>
                                <th className="p-3 text-left font-bold text-emerald-600 uppercase text-xs bg-emerald-50/50 dark:bg-emerald-900/10">Grid</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">Compute Model</td>
                                <td className="p-3 text-slate-500">Serverless Functions (cold starts)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Long-running Containers (no cold starts)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Execution Timeout</td>
                                <td className="p-3 text-slate-500">10s (Hobby) / 60s (Pro) / 900s (Enterprise)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Unlimited</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Persistent Storage</td>
                                <td className="p-3 text-slate-500">Blob / KV only (3rd party for volumes)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Native persistent volumes</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Database</td>
                                <td className="p-3 text-slate-500">Vercel Postgres/KV/Blob (paid addons)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Included Postgres + Redis</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Vendor Lock-in</td>
                                <td className="p-3 text-slate-500">High (Edge Runtime, ISR, proprietary APIs)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Zero (standard Docker)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Bandwidth Pricing</td>
                                <td className="p-3 text-slate-500">$0.15/GB (Enterprise only for overage)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Provider cost (often free up to 20TB)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Seat Pricing</td>
                                <td className="p-3 text-slate-500">$20/user/month</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Unlimited users</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>Migration Steps</h2>

                <h3>1. Export your data from Vercel</h3>
                <ul>
                    <li>Export your Vercel Postgres database using <code>vc db pull</code> or <code>pg_dump</code></li>
                    <li>Export Vercel KV data via the Redis CLI</li>
                    <li>Download any Vercel Blob storage contents</li>
                </ul>

                <h3>2. Convert your project for container deployment</h3>
                <p>Vercel serverless functions need to be wrapped in a small server. For Next.js:</p>
                <pre><code>{`// Instead of Vercel's serverless runtime,
// use next start or a custom server

// package.json
{
  "scripts": {
    "build": "next build",
    "start": "next start -p $PORT"
  }
}

// Dockerfile
FROM node:20-alpine
WORKDIR /app
COPY . .
RUN npm ci && npm run build
EXPOSE 3000
CMD ["npm", "start"]`}</code></pre>
                <p>Grid&apos;s Nixpacks builder can auto-detect Next.js — in most cases no Dockerfile is needed.</p>

                <h3>3. Deploy to Grid</h3>
                <ol>
                    <li>Push your code to GitHub</li>
                    <li>In Grid, click <strong>New Service</strong> and select your repository</li>
                    <li>Grid auto-detects the framework and builds</li>
                    <li>Your app is live with a <code>*.grid.app</code> URL</li>
                </ol>

                <h3>4. Migrate environment variables</h3>
                <p>Copy your environment variables from Vercel to Grid:</p>
                <pre><code>{`# In your project settings on Grid, add:
# All Vercel env vars (without VERCEL_ prefix)
DATABASE_URL=postgres://...
REDIS_URL=redis://...
# No need for VERCEL_URL, VERCEL_ENV, etc.`}</code></pre>

                <h3>5. Set up custom domain</h3>
                <ul>
                    <li>Configure your DNS to point to your Grid server IP</li>
                    <li>Grid (via Caddy) handles SSL automatically via Let&apos;s Encrypt</li>
                    <li>No need for Vercel&apos;s DNS propagation — just an A record</li>
                </ul>

                <h2>What changes with Next.js</h2>
                <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 mb-8">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Feature</th>
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">On Vercel</th>
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">On Grid</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">ISR (Incremental Static Regeneration)</td>
                                <td className="p-3 text-slate-500">Native</td>
                                <td className="p-3 text-slate-500">Works via <code>next start</code></td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Middleware (Edge)</td>
                                <td className="p-3 text-slate-500">Edge Runtime</td>
                                <td className="p-3 text-slate-500">Works on Node.js runtime</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Image Optimization</td>
                                <td className="p-3 text-slate-500">Managed</td>
                                <td className="p-3 text-slate-500">Built-in via sharp</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">WebSocket Support</td>
                                <td className="p-3 text-slate-500">Limited</td>
                                <td className="p-3 text-slate-500">Full support (long-running container)</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>What you gain</h2>
                <ul>
                    <li><strong>Zero cold starts</strong> — containers run 24/7</li>
                    <li><strong>Unlimited execution time</strong> — no 10s/60s timeout</li>
                    <li><strong>Persistent volumes</strong> — write to disk, not just blob</li>
                    <li><strong>No per-user seat pricing</strong> — invite your whole team</li>
                    <li><strong>Own your infrastructure</strong> — keep the AWS bill, not Vercel&apos;s markup</li>
                </ul>

                {/* Navigation */}
                <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                    <Link href="/docs/migration" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                        <ArrowLeft size={14} /> Migration Guides
                    </Link>
                    <Link href="/docs/migration/from-railway" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
                        Migrate from Railway <ArrowRight size={14} />
                    </Link>
                </div>
            </div>
        </main>
    );
}
