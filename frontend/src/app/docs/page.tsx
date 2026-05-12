import Link from 'next/link';
import { BookOpen, Download, Rocket, Terminal, Code2, Puzzle, ChevronRight } from 'lucide-react';

const docSections = [
  {
    href: '/docs/install',
    title: 'Installation & Operations',
    desc: 'Complete guide to install, update, manage, and troubleshoot your Grid instance. Covers IP mode, SSL setup, DNS, .env permissions, and all edge cases.',
    icon: Download,
    color: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    border: 'border-emerald-200 dark:border-emerald-800',
  },
  {
    href: '/docs/getting-started',
    title: 'Getting Started',
    desc: 'Deploy your first application in minutes. Connect your GitHub, create a service, and deploy.',
    icon: Rocket,
    color: 'text-blue-600 dark:text-blue-400',
    bg: 'bg-blue-50 dark:bg-blue-950/40',
    border: 'border-blue-200 dark:border-blue-800',
  },
  {
    href: '/docs/cli',
    title: 'CLI Reference',
    desc: 'Control Grid from your terminal. Deploy, view logs, SSH into containers, and more.',
    icon: Terminal,
    color: 'text-purple-600 dark:text-purple-400',
    bg: 'bg-purple-50 dark:bg-purple-950/40',
    border: 'border-purple-200 dark:border-purple-800',
  },
  {
    href: '/docs/api',
    title: 'API Reference',
    desc: 'Automate everything with our REST API. Fully documented via OpenAPI/Swagger.',
    icon: Code2,
    color: 'text-orange-600 dark:text-orange-400',
    bg: 'bg-orange-50 dark:bg-orange-950/40',
    border: 'border-orange-200 dark:border-orange-800',
  },
  {
    href: '/docs/addons',
    title: 'Addons',
    desc: 'Managed databases, caching, and storage — PostgreSQL, Redis, MongoDB, Qdrant.',
    icon: Puzzle,
    color: 'text-rose-600 dark:text-rose-400',
    bg: 'bg-rose-50 dark:bg-rose-950/40',
    border: 'border-rose-200 dark:border-rose-800',
  },
];

export default function DocsPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-16 px-4 bg-gradient-to-b from-emerald-50/60 to-white dark:from-emerald-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-4xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 text-xs font-semibold mb-4">
            <BookOpen size={14} /> Documentation
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-4">
            Grid Documentation
          </h1>
          <p className="text-lg text-slate-500 dark:text-slate-400 max-w-2xl mx-auto">
            Everything you need to install, configure, and manage your Grid platform.
          </p>
        </div>
      </section>

      <div className="max-w-5xl mx-auto px-4 py-16">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {docSections.map(section => {
            const Icon = section.icon;
            return (
              <Link
                key={section.href}
                href={section.href}
                className={`group flex flex-col p-6 rounded-xl border ${section.border} ${section.bg} hover:shadow-lg hover:-translate-y-0.5 transition-all`}
              >
                <div className="flex items-start gap-4">
                  <div className={`p-3 rounded-xl ${section.bg} ${section.color} flex-shrink-0`}>
                    <Icon size={24} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className={`text-lg font-bold ${section.color} mb-1.5 flex items-center gap-1.5`}>
                      {section.title}
                      <ChevronRight size={16} className="opacity-0 group-hover:opacity-100 -ml-1 transition-all" />
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

        <div className="mt-16 p-6 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800">
          <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-3">Still have questions?</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
            Check the <Link href="/docs/install#troubleshooting" className="text-emerald-600 dark:text-emerald-400 hover:underline">Troubleshooting</Link> section or visit the{' '}
            <a href="https://github.com/SMSLYCLOUD/smsly-hosting" target="_blank" rel="noopener noreferrer" className="text-emerald-600 dark:text-emerald-400 hover:underline">GitHub repository</a>.
          </p>
        </div>
      </div>
    </main>
  );
}
