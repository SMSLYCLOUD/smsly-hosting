import Link from 'next/link';
import { ArrowLeft, ArrowRight, Rocket } from 'lucide-react';

export default function GettingStartedPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-blue-50/60 to-white dark:from-blue-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-blue-600 dark:text-blue-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-blue-100 dark:bg-blue-900/50 rounded-xl">
              <Rocket className="w-5 h-5 text-blue-700 dark:text-blue-300" />
            </div>
            <span className="text-sm font-semibold text-blue-600 dark:text-blue-400 uppercase tracking-wider">Guide</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
            Getting Started
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl leading-relaxed">
            Deploy your first application on Grid.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <div className="docs-callout docs-callout-warning not-prose">
          <p className="!mt-0">
            <strong>Before you start:</strong> If you haven&apos;t installed Grid yet, follow the{' '}
            <Link href="/docs/install" className="text-amber-900 dark:text-amber-100 underline font-semibold">Installation Guide</Link> first.
          </p>
        </div>

        <h2>Prerequisites</h2>
        <ul>
          <li>A GitHub account</li>
          <li>Code ready to deploy (Node.js, Python, Go, Rust, etc.)</li>
          <li>Access to a running Grid instance (see <Link href="/docs/install">Installation Guide</Link>)</li>
        </ul>

        <h2>Step 1: Connect your account</h2>
        <p>Go to <strong>Settings &rarr; OAuth</strong> and connect your GitHub account. This allows Grid to access your repositories.</p>

        <h2>Step 2: Create a Service</h2>
        <p>Click <strong>&quot;New Service&quot;</strong> on the dashboard. Select your repository. Grid will auto-detect the framework.</p>

        <h2>Step 3: Deploy</h2>
        <p>Click <strong>&quot;Deploy&quot;</strong>. Grid will clone your repo, build it, and deploy it with a public URL. You&apos;ll see real-time build logs.</p>

        <h2>Next Steps</h2>
        <ul>
          <li>Configure a <Link href="/docs/install#domain--ssl-setup">custom domain with SSL</Link></li>
          <li>Set up <strong>environment variables</strong> in the service settings</li>
          <li>Add <Link href="/docs/addons">managed addons</Link> (PostgreSQL, Redis, etc.)</li>
        </ul>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> All Docs
          </Link>
          <Link href="/docs/cli" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
            CLI Reference <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </main>
  );
}
