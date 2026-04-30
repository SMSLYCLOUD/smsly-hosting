import Link from 'next/link';

const githubUrl = process.env.NEXT_PUBLIC_GITHUB_URL || '#';

export default function DownloadPage() {
  return (
    <main className="mx-auto min-h-screen max-w-4xl px-6 py-24 text-slate-100">
      <h1 className="text-4xl font-bold">Download & Install Cloud SMSLY</h1>
      <p className="mt-3 text-slate-300">Cloud SMSLY is free, open-source, and self-hosted. Install it on your own VPS with Docker.</p>

      <section className="mt-8 rounded-xl border border-slate-700 bg-slate-900/50 p-5">
        <h2 className="text-xl font-semibold">Quick install (Ubuntu VPS)</h2>
        <pre className="mt-3 overflow-x-auto rounded-md bg-black/50 p-4 text-sm">curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh{`\n`}sudo bash /tmp/install.sh</pre>
      </section>

      <section className="mt-6 rounded-xl border border-slate-700 bg-slate-900/50 p-5">
        <h2 className="text-xl font-semibold">Manual Docker Compose path</h2>
        <pre className="mt-3 overflow-x-auto rounded-md bg-black/50 p-4 text-sm">git clone https://github.com/SMSLYCLOUD/smsly-hosting.git /opt/smsly-hosting{`\n`}cd /opt/smsly-hosting{`\n`}cp .env.example .env{`\n`}docker compose -f docker-compose.prod.yml up -d --build</pre>
      </section>

      <section className="mt-6 rounded-xl border border-slate-700 bg-slate-900/50 p-5">
        <h2 className="text-xl font-semibold">Requirements</h2>
        <ul className="mt-3 list-disc space-y-1 pl-6 text-slate-300">
          <li>Ubuntu VPS recommended</li>
          <li>Docker + Docker Compose</li>
          <li>2 vCPU / 4 GB RAM minimum recommendation</li>
          <li>Domain and DNS are optional for first boot; needed for HTTPS production mode</li>
        </ul>
      </section>

      <section className="mt-6 flex flex-wrap gap-3">
        <a href={githubUrl} target="_blank" rel="noreferrer" className="rounded-md bg-emerald-500 px-4 py-2 font-semibold text-slate-950">View on GitHub</a>
        <Link href="/docs/install" className="rounded-md border border-slate-600 px-4 py-2">Install docs</Link>
        <Link href="/docs" className="rounded-md border border-slate-600 px-4 py-2">Documentation</Link>
      </section>

      <section className="mt-8 text-slate-300">
        <h2 className="text-xl font-semibold text-slate-100">Upgrade and troubleshooting</h2>
        <p className="mt-2">Use <code>sudo bash install.sh --update</code> for upgrades. See troubleshooting runbooks in docs for rollback and recovery workflows.</p>
      </section>
    </main>
  );
}
