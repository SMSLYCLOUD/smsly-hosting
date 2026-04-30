import Link from 'next/link';
import { Footer } from '@/components/layout/Footer';
import { StormCloudBackground } from '@/components/landing/StormCloudBackground';

const githubUrl = process.env.NEXT_PUBLIC_GITHUB_URL || '#';

const capabilities = [
  { name: 'Service deployments', status: 'Available', detail: 'Deploy and manage containerized services from the dashboard.' },
  { name: 'Domains + TLS', status: 'Available', detail: 'Domain and certificate management is exposed through service settings.' },
  { name: 'Environment variables', status: 'Available', detail: 'Per-service variables and secrets can be configured in settings tabs.' },
  { name: 'Logs + Console + Files', status: 'Available', detail: 'Operational debugging tabs are present for service runtime operations.' },
  { name: 'Backups + restore workflows', status: 'Experimental', detail: 'Backup and restore flows exist with ongoing hardening work.' },
  { name: 'Autoscaling', status: 'Experimental', detail: 'Autoscaling controls are present; production tuning should be validated per workload.' },
  { name: 'Topology / replication / mesh', status: 'Partial', detail: 'UI and docs exist, but deploy-by-deploy verification is still required.' },
];

export default function LandingPage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-slate-950 text-slate-100">
      <StormCloudBackground />
      <div className="relative z-10">
        <section className="mx-auto max-w-6xl px-6 pb-16 pt-28">
          <p className="inline-flex rounded-full border border-emerald-400/40 bg-emerald-400/10 px-4 py-1 text-xs font-semibold tracking-wide text-emerald-200">Free • Open Source • Self-Hosted • Docker Powered</p>
          <h1 className="mt-6 text-4xl font-bold leading-tight md:text-6xl">The Free, Open-Source PaaS for Your Own Cloud.</h1>
          <p className="mt-5 max-w-3xl text-base text-slate-200 md:text-lg">Cloud SMSLY is a self-hosted platform for deploying and operating apps on your own VPS. Free to run. Open to inspect. Built for your own infrastructure.</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/download" aria-label="Download and install Cloud SMSLY" className="rounded-md bg-emerald-500 px-5 py-3 font-semibold text-slate-950 hover:bg-emerald-400">Download / Install</Link>
            <a href={githubUrl} aria-label="View Cloud SMSLY on GitHub" target="_blank" rel="noreferrer" className="rounded-md border border-slate-400/40 bg-slate-900/50 px-5 py-3 font-semibold hover:bg-slate-800/70">View on GitHub</a>
            <Link href="/dashboard" aria-label="Open dashboard" className="rounded-md border border-slate-400/40 bg-slate-900/50 px-5 py-3 font-semibold hover:bg-slate-800/70">Open Dashboard</Link>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-6 py-8">
          <h2 className="text-2xl font-semibold">Core capabilities (transparent status)</h2>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {capabilities.map((item) => (
              <article key={item.name} className="rounded-xl border border-slate-700/60 bg-slate-900/40 p-5 backdrop-blur-sm">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-semibold">{item.name}</h3>
                  <span className="rounded-full border border-slate-500/50 px-2 py-0.5 text-xs">{item.status}</span>
                </div>
                <p className="mt-2 text-sm text-slate-300">{item.detail}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-6 py-8">
          <h2 className="text-2xl font-semibold">Why open source</h2>
          <p className="mt-3 max-w-4xl text-slate-300">No platform lock-in. Audit the code. Run it on your own VPS fleet. Extend workflows for your team, clients, providers, or affiliate infrastructure offerings.</p>
        </section>

        <Footer />
      </div>
    </main>
  );
}
