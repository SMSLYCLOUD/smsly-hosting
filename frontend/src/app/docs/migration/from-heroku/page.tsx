import Link from 'next/link';
import { ArrowLeft, ArrowRight, Container } from 'lucide-react';

export default function FromHerokuPage() {
    return (
        <main className="min-h-screen bg-white dark:bg-slate-950">
            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-purple-50 to-white dark:from-purple-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-3xl mx-auto">
                    <Link href="/docs/migration" className="inline-flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400 hover:underline mb-4">
                        <ArrowLeft size={14} /> Migration Guides
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-purple-100 dark:bg-purple-900/50 rounded-xl">
                            <Container className="w-5 h-5 text-purple-700 dark:text-purple-300" />
                        </div>
                        <span className="text-sm font-semibold text-purple-600 dark:text-purple-400 uppercase tracking-wider">Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3 leading-tight">
                        Migrating from Heroku
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
                        Move your Heroku apps to Grid. No more dyno sleeping, no more 512MB memory ceiling, no more 24h restarts.
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
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Heroku</th>
                                <th className="p-3 text-left font-bold text-emerald-600 uppercase text-xs bg-emerald-50/50 dark:bg-emerald-900/10">Grid</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">Dyno Model</td>
                                <td className="p-3 text-slate-500">Sleeps after 30min inactivity (free tier)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Always-on containers, no sleeping</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Memory Limit</td>
                                <td className="p-3 text-slate-500">512MB (Hobby) / 2.5GB (Standard)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">As much as your server has</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Build System</td>
                                <td className="p-3 text-slate-500">Buildpacks (proprietary)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Docker / Nixpacks (open)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Container Restarts</td>
                                <td className="p-3 text-slate-500">Every 24h (forced)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Only on deploy or failure</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Pricing</td>
                                <td className="p-3 text-slate-500">$7/dyno + $15/mo Postgres + $50/mo Redis</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Server cost only (often $4-8/mo)</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>Migration Steps</h2>

                <h3>1. Export Heroku data</h3>
                <pre><code>{`# Export Heroku Postgres
heroku pg:backups:capture --app your-app
heroku pg:backups:download --app your-app

# Export Redis
heroku redis:cli --app your-app
# Then use SAVE/redis-dump

# Save environment variables
heroku config --app your-app > heroku-config.txt`}</code></pre>

                <h3>2. Create a Dockerfile</h3>
                <p>Heroku buildpacks are proprietary. Convert your app to a Dockerfile:</p>
                <pre><code>{`# Example for a Node.js app
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
EXPOSE $PORT
CMD ["npm", "start"]`}</code></pre>
                <p>Or let Grid auto-detect with Nixpacks — no Dockerfile needed for most languages.</p>

                <h3>3. Deploy to Grid</h3>
                <ol>
                    <li>Push your repo to GitHub (if not already there)</li>
                    <li>Connect GitHub to Grid via OAuth</li>
                    <li>Create a new service and select your repository</li>
                    <li>Add your environment variables from <code>heroku-config.txt</code></li>
                    <li>Deploy</li>
                </ol>

                <h3>4. Migrate Heroku Postgres to Grid</h3>
                <pre><code>{`# Import your Heroku backup into Grid's managed Postgres
pg_restore --no-owner -d postgresql://grid:...@... latest.dump

# Or direct transfer
heroku pg:psql --app your-app -c "COPY ... TO STDOUT" | \\
  psql postgresql://grid:...@... -c "COPY ... FROM STDIN"`}</code></pre>

                <h3>5. Point your domain</h3>
                <ul>
                    <li>Update your DNS A record to your Grid server IP</li>
                    <li>Caddy automatically handles SSL certificates (Let&apos;s Encrypt)</li>
                    <li>Remove Heroku&apos;s DNS target — you own the domain directly</li>
                </ul>

                <h2>Heroku addon equivalents on Grid</h2>
                <div className="not-prose overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 mb-8">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Heroku Addon</th>
                                <th className="p-3 text-left font-bold text-slate-500 uppercase text-xs">Cost</th>
                                <th className="p-3 text-left font-bold text-emerald-600 uppercase text-xs bg-emerald-50/50 dark:bg-emerald-900/10">Grid Equivalent</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr>
                                <td className="p-3 font-medium">Heroku Postgres</td>
                                <td className="p-3 text-slate-500">$15/mo (Hobby)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Included (Patroni HA)</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Heroku Redis</td>
                                <td className="p-3 text-slate-500">$50/mo (Premium)</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Included</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Heroku Connect</td>
                                <td className="p-3 text-slate-500">$100/mo</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Direct Postgres connection</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">Heroku Scheduler</td>
                                <td className="p-3 text-slate-500">Free</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Celery Beat / Cron</td>
                            </tr>
                            <tr>
                                <td className="p-3 font-medium">SSL Certificate</td>
                                <td className="p-3 text-slate-500">Automated</td>
                                <td className="p-3 bg-emerald-50/10 dark:bg-emerald-900/5 font-medium">Automated (Let&apos;s Encrypt via Caddy)</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h2>What you gain</h2>
                <ul>
                    <li><strong>No dyno sleeping</strong> — apps are always-on, no cold starts</li>
                    <li><strong>No 24h forced restarts</strong> — containers run until you update them</li>
                    <li><strong>No memory ceilings</strong> — use your full server&apos;s resources</li>
                    <li><strong>Docker freedom</strong> — any runtime, any dependency, no buildpack limits</li>
                    <li><strong>90%+ cost reduction</strong> — Heroku&apos;s markup is extreme for what you get</li>
                </ul>

                {/* Navigation */}
                <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
                    <Link href="/docs/migration/from-railway" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
                        <ArrowLeft size={14} /> From Railway
                    </Link>
                    <Link href="/docs/migration/from-render" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
                        Migrate from Render <ArrowRight size={14} />
                    </Link>
                </div>
            </div>
        </main>
    );
}
