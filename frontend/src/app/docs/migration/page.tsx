import Link from 'next/link';
import { ArrowLeft, ArrowRight, Zap, Layers, Container, ArrowUpRight, Server } from 'lucide-react';

const platforms = [
    {
        from: 'Vercel',
        href: '/docs/migration/from-vercel',
        icon: Zap,
        color: 'text-black dark:text-white',
        bg: 'bg-zinc-100 dark:bg-zinc-800',
        border: 'border-zinc-200 dark:border-zinc-700',
        gradient: 'from-zinc-50 to-white dark:from-zinc-900 dark:to-slate-950',
        desc: 'Migrate your Next.js, frontend, and serverless functions to long-running containers with persistent volumes and zero cold starts.',
        steps: [
            'Connect your GitHub repo to Grid',
            'Deploy without cold starts',
            'No more function execution limits',
            'Persistent volumes instead of blob-only',
        ]
    },
    {
        from: 'Railway',
        href: '/docs/migration/from-railway',
        icon: Layers,
        color: 'text-black dark:text-white',
        bg: 'bg-zinc-100 dark:bg-zinc-800',
        border: 'border-zinc-200 dark:border-zinc-700',
        gradient: 'from-zinc-50 to-white dark:from-zinc-900 dark:to-slate-950',
        desc: 'Move your Railway containers to Grid. Same Docker workflow, same git-push DX — but on your own infrastructure at a fraction of the cost.',
        steps: [
            'Export your Railway volume data',
            'Deploy using the same Dockerfile',
            'Point your custom domain',
            'Save 80%+ on compute costs',
        ]
    },
    {
        from: 'Heroku',
        href: '/docs/migration/from-heroku',
        icon: Container,
        color: 'text-purple-600 dark:text-purple-400',
        bg: 'bg-purple-50 dark:bg-purple-950/40',
        border: 'border-purple-200 dark:border-purple-800',
        gradient: 'from-purple-50 to-white dark:from-purple-950/20 dark:to-slate-950',
        desc: 'Leave Heroku dynos behind. Deploy with no sleeping apps, no rigid buildpacks, and no forced container restarts every 24h.',
        steps: [
            'Migrate your dyno to a Dockerfile',
            'Transfer Heroku Postgres data',
            'No more dyno sleeping',
            'No more 512MB memory limit',
        ]
    },
    {
        from: 'Render',
        href: '/docs/migration/from-render',
        icon: ArrowUpRight,
        color: 'text-cyan-600 dark:text-cyan-400',
        bg: 'bg-cyan-50 dark:bg-cyan-950/40',
        border: 'border-cyan-200 dark:border-cyan-800',
        gradient: 'from-cyan-50 to-white dark:from-cyan-950/20 dark:to-slate-950',
        desc: 'Upgrade from Render with predictable pricing, faster builds, and no "sleeping" services on free/paid tiers.',
        steps: [
            'Convert render.yaml to grid config',
            'Deploy without sleeping',
            'Own your container registry',
            'Scale without the price hike',
        ]
    },
];

export default function MigrationPage() {
    return (
        <main className="min-h-screen bg-white dark:bg-slate-950">
            <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-amber-50/60 to-white dark:from-amber-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-4xl mx-auto">
                    <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400 hover:underline mb-6">
                        <ArrowLeft size={14} /> Back to Docs
                    </Link>
                    <div className="flex items-center gap-3 mb-4">
                        <div className="p-2.5 bg-amber-100 dark:bg-amber-900/50 rounded-xl">
                            <Server className="w-6 h-6 text-amber-700 dark:text-amber-300" />
                        </div>
                        <span className="text-sm font-semibold text-amber-600 dark:text-amber-400 uppercase tracking-wider">Guide</span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white mb-3">
                        Migration Guides
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl">
                        Move your applications from other PaaS platforms to Grid. Same developer experience, your own infrastructure.
                    </p>
                </div>
            </section>

            <div className="max-w-5xl mx-auto px-4 py-16">
                <div className="grid md:grid-cols-2 gap-6">
                    {platforms.map((platform) => {
                        const Icon = platform.icon;
                        return (
                            <Link
                                key={platform.from}
                                href={platform.href}
                                className={`group relative p-6 rounded-2xl border ${platform.border} bg-gradient-to-br ${platform.gradient} hover:shadow-lg hover:-translate-y-0.5 transition-all`}
                            >
                                <div className="flex items-start gap-4 mb-4">
                                    <div className={`p-3 rounded-xl ${platform.bg}`}>
                                        <Icon className={`w-6 h-6 ${platform.color}`} />
                                    </div>
                                    <div>
                                        <h3 className={`text-xl font-bold ${platform.color}`}>
                                            From {platform.from}
                                        </h3>
                                        <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold mt-0.5">Migration Guide</p>
                                    </div>
                                </div>
                                <p className="text-sm text-slate-600 dark:text-slate-400 mb-4 leading-relaxed">
                                    {platform.desc}
                                </p>
                                <ul className="space-y-2 mb-4">
                                    {platform.steps.map((step, j) => (
                                        <li key={j} className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-500">
                                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 flex-shrink-0" />
                                            {step}
                                        </li>
                                    ))}
                                </ul>
                                <div className="flex items-center gap-1 text-sm font-semibold text-emerald-600 dark:text-emerald-400 group-hover:gap-2 transition-all">
                                    Read Guide <ArrowRight size={14} />
                                </div>
                            </Link>
                        );
                    })}
                </div>

                <div className="mt-12 p-6 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800">
                    <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-3">Need help with a different platform?</h2>
                    <p className="text-sm text-slate-600 dark:text-slate-400">
                        Grid works with any platform that produces standard Docker containers.
                        Open an issue on{' '}
                        <a href="https://github.com/SMSLYCLOUD/smsly-hosting" target="_blank" rel="noopener noreferrer" className="text-emerald-600 dark:text-emerald-400 hover:underline">GitHub</a>
                        {' '}or ask in our community.
                    </p>
                </div>
            </div>
        </main>
    );
}
