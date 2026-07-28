import Link from 'next/link';
import { ArrowLeft, ArrowRight, Code2 } from 'lucide-react';

export default function ApiRefPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-orange-50/60 to-white dark:from-orange-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-orange-600 dark:text-orange-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-orange-100 dark:bg-orange-900/50 rounded-xl">
              <Code2 className="w-5 h-5 text-orange-700 dark:text-orange-300" />
            </div>
            <span className="text-sm font-semibold text-orange-600 dark:text-orange-400 uppercase tracking-wider">Reference</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
            API Reference
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
            Automate your infrastructure with the Grid REST API.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <p>
          The Grid API is fully documented using OpenAPI (Swagger).
          You can explore the schema or view the raw JSON specification.
        </p>

        <div className="not-prose my-8 p-8 rounded-xl bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700/50 text-center">
          <Code2 size={32} className="mx-auto text-slate-400 dark:text-slate-500 mb-3" />
          <p className="text-slate-600 dark:text-slate-400 font-medium">Interactive API docs coming soon.</p>
          <p className="text-sm text-slate-500 dark:text-slate-500 mt-2">
            Base URL: <code className="text-xs bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 rounded">https://api.cloud.smsly.cloud/api/v1</code>
          </p>
        </div>

        <p>
          For now, you can also use the <Link href="/docs/cli">CLI</Link> to manage services programmatically, or refer to the <Link href="/docs/install">Installation Guide</Link> for server management commands.
        </p>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs/cli" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> CLI Reference
          </Link>
          <Link href="/docs/addons" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
            Addons <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </main>
  );
}
