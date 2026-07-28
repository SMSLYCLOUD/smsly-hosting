import Link from 'next/link';
import { BookOpen, Download, Rocket, Terminal, Code2, Puzzle, ChevronRight, ArrowRight, Server, ArrowLeftRight, Network, Brain, Activity, Code, Zap, Github, Shield, History } from 'lucide-react';

const docSections = [
  {
    href: '/docs/install',
    title: 'Installation & Operations',
    desc: 'Complete guide to install, update, manage, and troubleshoot your Grid instance. Covers IP mode, SSL setup, DNS, .env permissions, and all edge cases.',
    icon: Download,
    color: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    border: 'border-emerald-200 dark:border-emerald-800/50',
  },
  {
    href: '/docs/getting-started',
    title: 'Getting Started',
    desc: 'Deploy your first application in minutes. Connect your GitHub, create a service, and deploy.',
    icon: Rocket,
    color: 'text-blue-600 dark:text-blue-400',
    bg: 'bg-blue-50 dark:bg-blue-950/40',
    border: 'border-blue-200 dark:border-blue-800/50',
  },
  {
    href: '/docs/github-app',
    title: 'GitHub App Setup',
    desc: 'Create and configure a GitHub App for automatic deployments, PR previews, and commit deployment statuses.',
    icon: Github,
    color: 'text-slate-700 dark:text-slate-300',
    bg: 'bg-slate-50 dark:bg-slate-900/60',
    border: 'border-slate-200 dark:border-slate-700/50',
  },
  {
    href: '/docs/migration',
    title: 'Migration Guides',
    desc: 'Move your apps from Vercel, Railway, Heroku, or Render to Grid. Step-by-step guides with data migration, config changes, and cost comparisons.',
    icon: ArrowRight,
    color: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50 dark:bg-amber-950/40',
    border: 'border-amber-200 dark:border-amber-800/50',
  },
  {
    href: '/docs/cli',
    title: 'CLI Reference',
    desc: 'Control Grid from your terminal. Deploy, view logs, SSH into containers, and more.',
    icon: Terminal,
    color: 'text-purple-600 dark:text-purple-400',
    bg: 'bg-purple-50 dark:bg-purple-950/40',
    border: 'border-purple-200 dark:border-purple-800/50',
  },
  {
    href: '/docs/api',
    title: 'API Reference',
    desc: 'Automate everything with our REST API. Fully documented via OpenAPI/Swagger.',
    icon: Code2,
    color: 'text-orange-600 dark:text-orange-400',
    bg: 'bg-orange-50 dark:bg-orange-950/40',
    border: 'border-orange-200 dark:border-orange-800/50',
  },
  {
    href: '/docs/transfers',
    title: 'Server Transfers',
    desc: 'Move services between nodes in your Grid fleet. Drag-and-drop in the UI, or drive the pipeline from the API.',
    icon: ArrowLeftRight,
    color: 'text-violet-600 dark:text-violet-400',
    bg: 'bg-violet-50 dark:bg-violet-950/40',
    border: 'border-violet-200 dark:border-violet-800/50',
  },
  {
    href: '/docs/multi-server',
    title: 'Multi-Server & Remote Deployment',
    desc: 'Span a primary, full-stack followers, and lightweight agents. One dashboard, one WireGuard mesh, one source of truth.',
    icon: Network,
    color: 'text-indigo-600 dark:text-indigo-400',
    bg: 'bg-indigo-50 dark:bg-indigo-950/40',
    border: 'border-indigo-200 dark:border-indigo-800/50',
  },
  {
    href: '/docs/deployments',
    title: 'Deployments',
    desc: 'Source to running container. Git, Docker, upload, template, or inline function. Every step observable, audit-logged, rollback-safe.',
    icon: Rocket,
    color: 'text-violet-600 dark:text-violet-400',
    bg: 'bg-violet-50 dark:bg-violet-950/40',
    border: 'border-violet-200 dark:border-violet-800/50',
  },
  {
    href: '/docs/ai',
    title: 'AI & Intelligence',
    desc: '17 model providers, multi-agent Senate Committee, Jules auto-fix. Opt-in: nothing enabled until an admin saves a key.',
    icon: Brain,
    color: 'text-purple-600 dark:text-purple-400',
    bg: 'bg-purple-50 dark:bg-purple-950/40',
    border: 'border-purple-200 dark:border-purple-800/50',
  },
  {
    href: '/docs/intelligence',
    title: 'Intelligence (Runtime)',
    desc: 'The always-on watchdog. Periodic anomaly scans, self-healing remediation, daily reports. No LLM required.',
    icon: Activity,
    color: 'text-cyan-600 dark:text-cyan-400',
    bg: 'bg-cyan-50 dark:bg-cyan-950/40',
    border: 'border-cyan-200 dark:border-cyan-800/50',
  },
  {
    href: '/docs/functions',
    title: 'Functions',
    desc: 'Inline source code in Node 18 or Python 3.9. A thin HTTP shim on a hardened container with an SSRF guard on every outbound call.',
    icon: Code,
    color: 'text-orange-600 dark:text-orange-400',
    bg: 'bg-orange-50 dark:bg-orange-950/40',
    border: 'border-orange-200 dark:border-orange-800/50',
  },
  {
    href: '/docs/autoscaling',
    title: 'Autoscaling',
    desc: 'Three engines, one shared state. CPU hysteresis, Prometheus + Loki + AI, and a K8s-style admin surface for manual control.',
    icon: Zap,
    color: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    border: 'border-emerald-200 dark:border-emerald-800/50',
  },
  {
    href: '/docs/addons',
    title: 'Addons',
    desc: 'Managed databases, caching, and storage — PostgreSQL, Redis, MongoDB, Qdrant.',
    icon: Puzzle,
    color: 'text-rose-600 dark:text-rose-400',
    bg: 'bg-rose-50 dark:bg-rose-950/40',
    border: 'border-rose-200 dark:border-rose-800/50',
  },
  {
    href: '/docs/security',
    title: 'Security',
    desc: 'Defense-in-depth architecture across 11 integrated layers. Container isolation, runtime monitoring, encryption, network security, and audit logging.',
    icon: Shield,
    color: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    border: 'border-emerald-200 dark:border-emerald-800/50',
  },
  {
    href: '/docs/changelog',
    title: 'Changelog',
    desc: 'Every release, every feature, every fix. Open-source and transparent version history.',
    icon: History,
    color: 'text-slate-600 dark:text-slate-400',
    bg: 'bg-slate-50 dark:bg-slate-900/60',
    border: 'border-slate-200 dark:border-slate-700/50',
  },
];

export default function DocsPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-14 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 text-xs font-semibold tracking-wide uppercase mb-6">
            <BookOpen size={14} /> Documentation
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-5 leading-tight">
            Grid Documentation
          </h1>
          <p className="text-lg md:text-xl text-slate-500 dark:text-slate-400 max-w-xl mx-auto leading-relaxed">
            Everything you need to install, configure, and manage your Grid platform.
          </p>
        </div>
      </section>

      <div className="max-w-5xl mx-auto px-4 py-16">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {docSections.map(section => {
            const Icon = section.icon;
            return (
              <Link
                key={section.href}
                href={section.href}
                className={`group flex flex-col p-5 rounded-xl border ${section.border} ${section.bg} hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200`}
              >
                <div className="flex items-start gap-4">
                  <div className={`p-2.5 rounded-lg ${section.bg} ${section.color} flex-shrink-0`}>
                    <Icon size={20} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className={`text-base font-bold ${section.color} mb-1 flex items-center gap-1.5`}>
                      {section.title}
                      <ChevronRight size={14} className="opacity-0 group-hover:opacity-100 -ml-1 transition-all" />
                    </h3>
                    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
                      {section.desc}
                    </p>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>

        <div className="mt-16 p-6 rounded-xl bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800">
          <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-2">Still have questions?</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
            Check the <Link href="/docs/install#troubleshooting" className="text-emerald-600 dark:text-emerald-400 hover:underline font-medium">Troubleshooting</Link> section or visit the{' '}
            <a href="https://github.com/SMSLYCLOUD/smsly-hosting" target="_blank" rel="noopener noreferrer" className="text-emerald-600 dark:text-emerald-400 hover:underline font-medium">GitHub repository</a>.
          </p>
        </div>
      </div>
    </main>
  );
}
