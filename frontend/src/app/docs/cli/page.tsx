import Link from 'next/link';
import { ArrowLeft, ArrowRight, Terminal } from 'lucide-react';

export default function CLIPage() {
  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <section className="pt-28 pb-10 px-4 bg-gradient-to-b from-purple-50/60 to-white dark:from-purple-950/20 dark:to-slate-950 border-b border-slate-200 dark:border-slate-800">
        <div className="max-w-4xl mx-auto">
          <Link href="/docs" className="inline-flex items-center gap-1.5 text-sm text-purple-600 dark:text-purple-400 hover:underline mb-6">
            <ArrowLeft size={14} /> Back to Docs
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2.5 bg-purple-100 dark:bg-purple-900/50 rounded-xl">
              <Terminal className="w-6 h-6 text-purple-700 dark:text-purple-300" />
            </div>
            <span className="text-sm font-semibold text-purple-600 dark:text-purple-400 uppercase tracking-wider">Reference</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white mb-3">
            CLI Reference
          </h1>
          <p className="text-slate-500 dark:text-slate-400 text-lg max-w-2xl">
            Manage Grid from your terminal using the <code className="text-purple-600 dark:text-purple-400">cn</code> CLI.
          </p>
        </div>
      </section>

      <div className="max-w-3xl mx-auto px-4 py-12 prose prose-slate dark:prose-invert">
        <h2>Installation</h2>
        <pre><code>npm install -g grid-cli</code></pre>

        <h2>Commands</h2>
        <ul>
          <li><code>cn login</code> — Authenticate with your Grid instance</li>
          <li><code>cn deploy</code> — Deploy the current directory</li>
          <li><code>cn logs</code> — View runtime logs for a service</li>
          <li><code>cn ssh</code> — SSH into a running container</li>
        </ul>

        <h2>Server Management Commands</h2>
        <p>These commands require SSH access to the Grid server itself:</p>
        <pre><code># Install / Update
sudo bash /opt/smsly-hosting/install.sh --update

# Check container status
docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps

# View logs
docker compose -f /opt/smsly-hosting/docker-compose.prod.yml logs -f backend</code></pre>

        <p>
          See the <Link href="/docs/install">Installation Guide</Link> for detailed server management instructions.
        </p>

        {/* Navigation */}
        <div className="not-prose mt-12 pt-8 border-t border-slate-200 dark:border-slate-700 flex justify-between">
          <Link href="/docs/getting-started" className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            <ArrowLeft size={14} /> Getting Started
          </Link>
          <Link href="/docs/api" className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 hover:underline font-medium">
            API Reference <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </main>
  );
}
