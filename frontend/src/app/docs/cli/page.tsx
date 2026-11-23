import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Terminal } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';

export const metadata: Metadata = {
  title: 'CLI Reference',
  description:
    'Operate Trulay Grid from your terminal using the smsly CLI: authenticate, deploy, inspect logs, manage environment variables, and roll back releases.',
  alternates: { canonical: '/docs/cli' },
};

export default function CLIPage() {
  return (
    <div className="bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-purple-50/70 to-white dark:from-purple-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-3xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 min-h-[44px] text-sm text-purple-700 dark:text-purple-400 hover:underline mb-4">
            <ArrowLeft size={14} aria-hidden="true" /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-purple-100 dark:bg-purple-900/50 rounded-xl">
              <Terminal className="w-5 h-5 text-purple-700 dark:text-purple-300" aria-hidden="true" />
            </div>
            <span className="text-sm font-semibold text-purple-700 dark:text-purple-400 uppercase tracking-wider">Reference</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
            CLI Reference
          </h1>
          <p className="text-slate-600 dark:text-slate-300 text-lg max-w-2xl leading-relaxed">
            Operate your Grid installation from the terminal with the{' '}
            <code className="text-purple-700 dark:text-purple-400 font-semibold">smsly</code> CLI.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert prose-headings:scroll-mt-24">
        <h2>Installation</h2>
        <p>Install the CLI package from Python (keeps <code>smsly</code> executable compatibility across CI scripts):</p>
        <CodeBlock>{`pip install smsly-cli`}</CodeBlock>

        <h2>Authentication</h2>
        <p>Create an API token in Settings and authenticate your terminal session:</p>
        <CodeBlock>{`smsly login --token "smsly_your_token_here"`}</CodeBlock>

        <h2>Commands</h2>
        <ul>
          <li><code>smsly deploy</code> &mdash; Deploy the current working directory to your connected instance</li>
          <li><code>smsly logs &lt;service_id&gt;</code> &mdash; Stream or tail deployment and container logs</li>
          <li><code>smsly rollback &lt;service_id&gt; &lt;commit_hash&gt;</code> &mdash; Revert a service to a prior revision</li>
          <li><code>smsly env set KEY=VALUE</code> &mdash; Set environment variables on a target service</li>
          <li><code>smsly env ls</code> &mdash; List configured environment variables</li>
        </ul>

        <h2>Server Management Commands</h2>
        <p>On the control-plane host over SSH:</p>
        <CodeBlock>{`# Install or update platform stack
sudo bash /opt/smsly-hosting/install.sh --update

# Check container status
docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps

# View backend logs
docker compose -f /opt/smsly-hosting/docker-compose.prod.yml logs -f backend`}</CodeBlock>

        <p>
          See the <Link href="/docs/install">Installation Guide</Link> for full host setup and recovery workflows.
        </p>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs/getting-started" className="inline-flex items-center gap-1.5 min-h-[44px] text-sm text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white">
            <ArrowLeft size={14} aria-hidden="true" /> Getting Started
          </Link>
          <Link href="/docs/api" className="inline-flex items-center gap-1.5 min-h-[44px] text-sm text-emerald-700 dark:text-emerald-400 hover:underline font-medium">
            API Reference <ArrowRight size={14} aria-hidden="true" />
          </Link>
        </div>
      </div>
    </div>
  );
}
