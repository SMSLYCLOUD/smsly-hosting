import Link from 'next/link';
import { ArrowLeft, Puzzle, Database, Braces, Globe } from 'lucide-react';

const addons = [
  {
    name: 'PostgreSQL',
    desc: 'Relational database with full SQL support. Ideal for structured data, transactional workloads, and analytics.',
    icon: Database,
    color: 'text-blue-600 dark:text-blue-400',
    bg: 'bg-blue-50 dark:bg-blue-950/40',
  },
  {
    name: 'Redis',
    desc: 'In-memory key-value store. Used for caching, session storage, real-time messaging, and rate limiting.',
    icon: Braces,
    color: 'text-red-600 dark:text-red-400',
    bg: 'bg-red-50 dark:bg-red-950/40',
  },
  {
    name: 'MongoDB',
    desc: 'NoSQL document database. Great for flexible schemas, JSON-like documents, and rapid prototyping.',
    icon: Database,
    color: 'text-green-600 dark:text-green-400',
    bg: 'bg-green-50 dark:bg-green-950/40',
  },
  {
    name: 'Qdrant',
    desc: 'Vector database for AI-powered search, semantic similarity, and embedding-based retrieval.',
    icon: Globe,
    color: 'text-purple-600 dark:text-purple-400',
    bg: 'bg-purple-50 dark:bg-purple-950/40',
  },
];

export default function AddonsPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-rose-50/60 to-white dark:from-rose-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-rose-600 dark:text-rose-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-rose-100 dark:bg-rose-900/50 rounded-xl">
              <Puzzle className="w-5 h-5 text-rose-700 dark:text-rose-300" />
            </div>
            <span className="text-sm font-semibold text-rose-600 dark:text-rose-400 uppercase tracking-wider">Addons</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
            Addons
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
            Managed infrastructure addons for your services.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <p>
          Addons are managed infrastructure services that you can provision alongside your applications.
          They are automatically networked with your services and include automated backups and monitoring.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 not-prose my-8">
          {addons.map(addon => {
            const Icon = addon.icon;
            return (
              <div key={addon.name} className={`p-5 rounded-xl border border-slate-200 dark:border-slate-700/50 ${addon.bg}`}>
                <div className="flex items-center gap-3 mb-3">
                  <div className={`p-2 rounded-lg ${addon.bg}`}>
                    <Icon size={18} className={addon.color} />
                  </div>
                  <h3 className={`font-bold text-sm ${addon.color}`}>{addon.name}</h3>
                </div>
                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{addon.desc}</p>
              </div>
            );
          })}
        </div>

        <h2>Provisioning</h2>
        <p>
          Addons can be provisioned via the <strong>&quot;Addons&quot;</strong> tab in your service dashboard or through the API.
          Each addon is deployed as a separate Docker container within the <code>smsly-net</code> network and is automatically
          linked to your service via environment variables.
        </p>

        <div className="docs-callout docs-callout-warning not-prose">
          <p className="!mt-0">
            <strong>Note:</strong> Addon provisioning requires the backend services to be running. See the{' '}
            <Link href="/docs/install" className="underline font-semibold">Installation Guide</Link> for setup instructions.
          </p>
        </div>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700">
          <Link href="/docs/api" className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> API Reference
          </Link>
        </div>
      </div>
    </main>
  );
}
