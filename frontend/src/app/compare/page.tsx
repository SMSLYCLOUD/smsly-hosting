import type { Metadata } from 'next';
import { Fragment } from 'react';
import Link from 'next/link';
import {
  Check, X, Minus, Zap, Cloud, Shield, Layers, ArrowRight,
  CheckCircle2, ArrowUpRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';

export const metadata: Metadata = {
  title: 'Compare Grid vs Railway, Vercel & Render',
  description:
    'Feature-by-feature comparison of Trulay Grid vs Railway, Vercel, and Render: pricing model, persistent storage, HA Postgres, WireGuard mesh, and vendor lock-in.',
  alternates: { canonical: '/compare' },
};

const battleCards = [
  {
    name: 'Grid',
    logo: Cloud,
    type: 'Sovereign PaaS',
    price: '$0',
    priceDetail: 'Open Source & Free',
    description: 'Your infrastructure, your rules. Full-stack ownership on any VPS.',
    features: [
      'Self-Hosted (Hetzner, AWS, DO, bare metal)',
      'Zero Vendor Lock-in (Standard Docker)',
      'Predictive AI Auto-Scaling',
      'Persistent Volumes & Patroni HA Included',
    ],
    highlight: true,
  },
  {
    name: 'Railway',
    logo: Layers,
    type: 'Managed PaaS',
    price: '$5 + Usage',
    priceDetail: 'RAM/CPU Minutes',
    description: 'Great DX, but usage-based compute and storage scale linearly with traffic.',
    features: [
      'Managed Cloud (GCP backbone)',
      'Low Vendor Lock-in',
      'Reactive Auto-Scaling',
      'Usage-billed Persistent Volumes',
    ],
    highlight: false,
  },
  {
    name: 'Vercel',
    logo: Zap,
    type: 'Frontend Cloud',
    price: '$20/seat',
    priceDetail: '+ Bandwidth/Function fees',
    description: 'Optimized for Next.js, restrictive for long-running backend workloads.',
    features: [
      'AWS Wrapper (Serverless)',
      'High Vendor Lock-in',
      'Cold Starts on Serverless Functions',
      'Requires 3rd-Party Persistent Storage',
    ],
    highlight: false,
  },
  {
    name: 'Render',
    logo: ArrowUpRight,
    type: 'Managed PaaS',
    price: '$25+',
    priceDetail: 'per standard instance',
    description: 'Easy starter workflow, with per-service upgrades and metered egress.',
    features: [
      'Free tier spins down when idle',
      'Per-instance tier jumps on scaling',
      'Managed cloud regions only',
      'Additional charges for HA & bandwidth',
    ],
    highlight: false,
  },
];

const comparisonRows = [
  {
    category: 'Core Platform',
    items: [
      { name: 'Deployment Target', cn: 'Any VPS / Cloud / Bare Metal', rw: 'Managed GCP', vc: 'Managed AWS Lambda', rn: 'Managed AWS/GCP' },
      { name: 'Compute Model', cn: 'Long-running Containers', rw: 'Containers', vc: 'Serverless Functions', rn: 'Containers / Instances' },
      { name: 'Vendor Lock-in', cn: 'Zero (Standard Docker)', rw: 'Low', vc: 'High (Proprietary APIs)', rn: 'Medium (Proprietary YAML)' },
      { name: 'Multi-Cloud Support', cn: true, rw: false, vc: false, rn: false },
    ],
  },
  {
    category: 'Pricing & Limits',
    items: [
      { name: 'Pricing Model', cn: 'Flat Rate (Pay VPS provider directly)', rw: 'Usage-based markup', vc: 'Per-seat + Usage + Bandwidth', rn: 'Per Service Tier + Usage' },
      { name: 'Bandwidth Cost', cn: 'Provider allowance (often TB free)', rw: '$0.10/GB after limit', vc: '$0.15/GB (Enterprise only)', rn: '$0.10/GB after limit' },
      { name: 'Execution Time Limit', cn: 'Unlimited', rw: 'Unlimited', vc: '10s - 60s (Plan dependent)', rn: 'Unlimited (paid tiers)' },
      { name: 'Seat Pricing', cn: 'Unlimited Users', rw: 'Unlimited', vc: '$20/user/month', rn: '$19/user/month' },
    ],
  },
  {
    category: 'Features',
    items: [
      { name: 'AI Auto-Scaling', cn: 'Predictive (AI-Driven)', rw: 'Reactive', vc: 'Reactive (Serverless)', rn: 'Reactive' },
      { name: 'Persistent Storage', cn: 'Native Volumes (Zero Fee)', rw: 'Usage-billed Volumes', vc: '3rd Party Integrations Only', rn: 'Paid Disk Addons' },
      { name: 'Private Networking', cn: 'Included (WireGuard Mesh)', rw: 'Included', vc: 'Enterprise Only', rn: 'Included (Private Service)' },
      { name: 'Docker Support', cn: 'Native (Dockerfile / Compose)', rw: 'Native', vc: 'Limited (Next.js focused)', rn: 'Native' },
      { name: 'Serverless Functions (FaaS)', cn: 'Monaco Editor + Node/Python', rw: false, vc: 'Serverless Functions (JS/TS)', rn: false },
      { name: 'Container Terminal', cn: 'Web SSH into containers', rw: false, vc: false, rn: 'Paid tiers' },
    ],
  },
  {
    category: 'Databases & State',
    items: [
      { name: 'Managed Postgres', cn: 'Included (Patroni HA)', rw: 'Included (Single node)', vc: 'Vercel Postgres (Paid)', rn: 'Render Postgres (Paid)' },
      { name: 'Vector Search (pgvector)', cn: 'Included by default', rw: 'Manual setup', vc: '3rd party integrations', rn: 'Manual setup' },
      { name: 'Redis / In-Memory', cn: 'Included (Sentinel HA)', rw: 'Included', vc: 'Vercel KV (Paid)', rn: 'Render Key Value (Paid)' },
      { name: 'S3-Compatible Backups', cn: 'S3, R2, MinIO (Built-in)', rw: false, vc: false, rn: 'Snapshots only' },
    ],
  },
  {
    category: 'Networking & Security',
    items: [
      { name: 'VPN Mesh', cn: 'WireGuard Node-to-Node', rw: false, vc: 'Enterprise Only', rn: 'VPC Only' },
      { name: 'Custom Domains & SSL', cn: 'Unlimited (Caddy Proxy)', rw: 'Included', vc: 'Included', rn: 'Included' },
      { name: 'DDoS Protection', cn: 'Cloudflare Compatible', rw: 'Included', vc: 'Included', rn: 'Included' },
    ],
  },
  {
    category: 'AI & LLM Workloads',
    items: [
      { name: 'Ollama / Local LLMs', cn: 'Native Blueprints (1-Click)', rw: 'Manual Setup', vc: false, rn: 'Manual Setup' },
      { name: 'GPU Support', cn: 'Any provider (AWS/Hetzner)', rw: false, vc: false, rn: false },
      { name: 'AI Request Routing', cn: 'Included (Senate 17 providers)', rw: false, vc: 'Vercel AI SDK (Code level)', rn: false },
    ],
  },
  {
    category: 'Deployment & DX',
    items: [
      { name: 'Git Push to Deploy', cn: true, rw: true, vc: true, rn: true },
      { name: 'Multi-Git Providers', cn: 'GitHub, GitLab, Bitbucket', rw: 'GitHub Only', vc: 'GitHub, GitLab, Bitbucket', rn: 'GitHub, GitLab, Bitbucket' },
      { name: 'Nixpacks Build Support', cn: 'Auto-detect any language', rw: true, vc: false, rn: false },
      { name: 'One-Click Blueprints', cn: 'Templates & Addons', rw: 'Templates Available', vc: 'Templates Available', rn: 'Blueprints Available' },
      { name: 'CLI Tooling', cn: 'smsly CLI', rw: 'railway CLI', vc: 'vercel CLI', rn: 'render CLI' },
    ],
  },
];

function renderCell(val: string | boolean, isPrimary = false) {
  if (typeof val === 'boolean') {
    return val ? (
      <span className="inline-flex items-center">
        <Check className="w-5 h-5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
        <span className="sr-only">Included</span>
      </span>
    ) : (
      <span className="inline-flex items-center">
        <X className="w-5 h-5 text-slate-500 dark:text-slate-400" aria-hidden="true" />
        <span className="sr-only">Not included</span>
      </span>
    );
  }
  return <span className={isPrimary ? 'font-semibold text-slate-900 dark:text-white' : undefined}>{val}</span>;
}

export default function ComparePage() {
  return (
    <div className="bg-slate-50 dark:bg-slate-950">
      {/* HERO SECTION — Explicit dark showcase preserved in both themes */}
      <section className="relative pt-28 pb-24 px-6 overflow-hidden bg-slate-950 text-white">
        <div className="max-w-7xl mx-auto text-center relative z-10">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-500/15 border border-emerald-500/30 rounded-full mb-8">
            <Shield className="w-4 h-4 text-emerald-400" aria-hidden="true" />
            <span className="text-sm font-semibold text-emerald-300 uppercase tracking-wide">
              The Sovereign Alternative
            </span>
          </div>

          <h1 className="text-4xl md:text-6xl lg:text-7xl font-extrabold mb-7 tracking-tight text-white">
            Stop Paying the <br className="hidden md:block" />
            <span className="text-emerald-400">&quot;Cloud Tax&quot;</span>
          </h1>

          <p className="text-xl text-slate-300 max-w-3xl mx-auto leading-relaxed mb-10">
            Why pay steep markups for managed PaaS tiers? <br className="hidden md:block" />
            Grid gives you automated deployments with raw VPS economics.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button
              size="lg"
              asChild
              className="bg-emerald-700 hover:bg-emerald-600 text-white font-bold px-8 h-12 rounded-xl text-base"
            >
              <Link href="/register">
                Start Deploying Free <ArrowRight className="ml-2 w-5 h-5" aria-hidden="true" />
              </Link>
            </Button>
            <Button
              variant="outline"
              size="lg"
              asChild
              className="border-slate-600 bg-transparent text-white hover:bg-slate-800 h-12 rounded-xl text-base"
            >
              <Link href="#comparison-table">View Full Comparison</Link>
            </Button>
          </div>
        </div>
      </section>

      {/* BATTLE CARDS */}
      <section className="py-16 md:py-20 px-6 relative lg:-mt-12 z-20">
        <div className="max-w-7xl mx-auto">
          <h2 className="sr-only">Platform Comparison Overview</h2>
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
            {battleCards.map((card) => (
              <div
                key={card.name}
                className={`relative p-7 rounded-2xl border-2 flex flex-col ${
                  card.highlight
                    ? 'bg-slate-900 border-emerald-500 text-white'
                    : 'bg-card border-border text-card-foreground'
                }`}
              >
                {card.highlight && (
                  <div className="inline-block self-start mb-4 px-3 py-1 bg-emerald-700 text-white text-xs font-bold uppercase tracking-wider rounded-full">
                    Self-Hosted Choice
                  </div>
                )}

                <div className="mb-5 flex items-center justify-between">
                  <div
                    className={`p-3 rounded-xl ${
                      card.highlight ? 'bg-emerald-500/15 text-emerald-400' : 'bg-muted text-slate-700 dark:text-slate-300'
                    }`}
                  >
                    <card.logo className="w-7 h-7" aria-hidden="true" />
                  </div>
                  <div className="text-right">
                    <h3 className={`text-xl font-bold ${card.highlight ? 'text-white' : 'text-slate-900 dark:text-white'}`}>
                      {card.name}
                    </h3>
                    <p className={`text-xs font-medium uppercase tracking-wide ${card.highlight ? 'text-slate-300' : 'text-slate-600 dark:text-slate-400'}`}>
                      {card.type}
                    </p>
                  </div>
                </div>

                <div className="mb-6">
                  <span className={`text-3xl font-extrabold tabular-nums ${card.highlight ? 'text-white' : 'text-slate-900 dark:text-white'}`}>
                    {card.price}
                  </span>
                  <span className={`block text-sm mt-1 ${card.highlight ? 'text-slate-300' : 'text-slate-600 dark:text-slate-400'}`}>
                    {card.priceDetail}
                  </span>
                </div>

                <p className={`text-sm mb-6 leading-relaxed ${card.highlight ? 'text-slate-300' : 'text-slate-600 dark:text-slate-400'}`}>
                  {card.description}
                </p>

                <ul className="space-y-3 mb-6 flex-1">
                  {card.features.map((feat) => (
                    <li
                      key={feat}
                      className={`flex items-start gap-2.5 text-sm font-medium ${
                        card.highlight ? 'text-slate-200' : 'text-slate-700 dark:text-slate-300'
                      }`}
                    >
                      {card.highlight ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" aria-hidden="true" />
                      ) : (
                        <Minus className="w-4 h-4 text-slate-500 shrink-0 mt-0.5" aria-hidden="true" />
                      )}
                      <span>{feat}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* DETAILED COMPARISON TABLE */}
      <section id="comparison-table" className="py-20 px-6 bg-white dark:bg-slate-950 scroll-mt-20">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-12">
            <h2 className="text-3xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-4">Feature by Feature</h2>
            <p className="text-lg md:text-xl text-slate-600 dark:text-slate-300 max-w-2xl mx-auto">
              See what you get with Grid on your own VPS compared to managed platforms.
            </p>
          </div>

          <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800 bg-card">
            <table className="w-full min-w-[680px] text-left border-collapse">
              <thead>
                <tr className="bg-slate-100 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                  <th scope="col" className="sticky left-0 z-10 bg-slate-100 dark:bg-slate-900 p-5 text-xs font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider w-1/5">
                    Feature
                  </th>
                  <th scope="col" className="p-5 text-base font-bold text-emerald-800 dark:text-emerald-400 w-1/5 bg-emerald-50 dark:bg-emerald-950/30 border-x border-emerald-200 dark:border-emerald-900/40">
                    Grid
                  </th>
                  <th scope="col" className="p-5 text-base font-bold text-slate-800 dark:text-slate-200 w-1/5">
                    Railway
                  </th>
                  <th scope="col" className="p-5 text-base font-bold text-slate-800 dark:text-slate-200 w-1/5">
                    Vercel
                  </th>
                  <th scope="col" className="p-5 text-base font-bold text-slate-800 dark:text-slate-200 w-1/5">
                    Render
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {comparisonRows.map((section) => (
                  <Fragment key={section.category}>
                    <tr className="bg-slate-100/80 dark:bg-slate-900/80">
                      <th
                        colSpan={5}
                        scope="colgroup"
                        className="px-5 py-3 text-xs font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider"
                      >
                        {section.category}
                      </th>
                    </tr>
                    {section.items.map((row) => (
                      <tr key={row.name} className="hover:bg-muted/40 transition-colors">
                        <th
                          scope="row"
                          className="sticky left-0 z-10 bg-card px-5 py-4 text-sm font-semibold text-slate-900 dark:text-white border-r border-slate-200 dark:border-slate-800"
                        >
                          {row.name}
                        </th>
                        <td className="px-5 py-4 text-sm bg-emerald-50/40 dark:bg-emerald-950/15 border-x border-emerald-100 dark:border-emerald-900/30">
                          {renderCell(row.cn, true)}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700 dark:text-slate-300">
                          {renderCell(row.rw)}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700 dark:text-slate-300 border-x border-slate-200 dark:border-slate-800">
                          {renderCell(row.vc)}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-700 dark:text-slate-300">
                          {renderCell(row.rn)}
                        </td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* MIGRATION CTA */}
      <section className="py-20 px-6 bg-slate-950 text-white border-t border-slate-800">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl md:text-5xl font-extrabold text-white mb-5">Ready to make the switch?</h2>
          <p className="text-lg md:text-xl text-slate-300 mb-9 max-w-2xl mx-auto">
            Migrating from Vercel, Railway, Heroku, or Render takes minutes. Connect your GitHub, point to your VPS, and deploy.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button size="lg" asChild className="bg-emerald-700 hover:bg-emerald-600 text-white font-bold px-8 h-12 rounded-xl text-base">
              <Link href="/register">Get Started Now</Link>
            </Button>
            <Button variant="outline" size="lg" asChild className="border-slate-600 bg-transparent text-white hover:bg-slate-800 h-12 rounded-xl text-base">
              <Link href="/docs/migration">Read Migration Guide</Link>
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
