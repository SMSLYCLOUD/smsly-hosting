import type { Metadata } from 'next';
import Link from 'next/link';
import { Check, Shield, Zap } from 'lucide-react';
import { PricingFaq } from '@/components/pricing/PricingFaq';

export const metadata: Metadata = {
  title: 'Pricing',
  description:
    'Trulay Grid is 100% free and MIT open source. Self-host unlimited servers, apps, PostgreSQL HA, and WireGuard meshes on your own VPS with zero seat fees.',
  alternates: { canonical: '/pricing' },
};

const faqs = [
  {
    q: 'Is Grid really free?',
    a: 'Yes. Grid is MIT open source and free to use. You deploy it on your own infrastructure—you only pay your cloud or VPS provider directly for your servers.',
  },
  {
    q: 'What counts as a "server"?',
    a: 'A server is any VPS, dedicated machine, or cloud VM connected to Grid. There are no artificial caps on the number of nodes you can orchestrate.',
  },
  {
    q: 'Is there a catch or paid feature wall?',
    a: 'No catch. High availability, WireGuard mesh, AI autoscaling, and addon provisioning are included in the core open-source edition.',
  },
  {
    q: 'What happens if I exceed an application limit?',
    a: 'There are no application count or seat limits. Deploy as many applications and databases as your server hardware can handle.',
  },
  {
    q: 'Can I contribute to the project?',
    a: 'Yes! Grid is open source under the MIT License and welcomes issues and pull requests in the GitHub repository.',
  },
];

export default function PricingPage() {
  const faqJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faqs.map((f) => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: {
        '@type': 'Answer',
        text: f.a,
      },
    })),
  };

  return (
    <div className="bg-slate-50 dark:bg-slate-950">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }}
      />

      {/* Hero */}
      <section className="pt-28 pb-12 px-4 text-center">
        <div className="max-w-4xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-800 dark:text-emerald-300 text-sm font-semibold rounded-full mb-6">
            <Zap className="w-3.5 h-3.5" aria-hidden="true" />
            <span>Transparent Open Source</span>
          </div>
          <h1 className="text-4xl md:text-6xl font-extrabold text-slate-900 dark:text-white mb-6">
            100% Open Source. 100% Free.
          </h1>
          <p className="text-lg md:text-xl text-slate-600 dark:text-slate-300 max-w-2xl mx-auto">
            Grid is a self-hosted control plane. Deploy it on your own hardware and keep 100% of the control—no subscriptions, no per-seat billing, no cloud tax.
          </p>
        </div>
      </section>

      {/* Pricing Card */}
      <section className="pb-20 px-4">
        <div className="max-w-lg mx-auto">
          <div className="relative rounded-2xl p-8 border-2 border-emerald-600 dark:border-emerald-500 bg-card flex flex-col">
            <div className="inline-block self-start px-3 py-1 bg-emerald-700 text-white text-xs font-bold rounded-full uppercase tracking-wider mb-5">
              Free Forever
            </div>

            <div className="flex items-center gap-4 mb-5">
              <div className="w-12 h-12 rounded-xl bg-emerald-700 flex items-center justify-center shrink-0">
                <Shield className="w-6 h-6 text-white" aria-hidden="true" />
              </div>
              <div>
                <h2 className="text-2xl font-bold text-slate-900 dark:text-white">Open Source Edition</h2>
                <p className="text-xs text-slate-600 dark:text-slate-400">MIT Licensed</p>
              </div>
            </div>

            <div className="mb-4">
              <span className="text-5xl font-extrabold text-slate-900 dark:text-white tabular-nums">$0</span>
              <span className="text-slate-600 dark:text-slate-400 text-sm ml-1.5">/ forever</span>
            </div>
            <p className="text-base text-slate-600 dark:text-slate-300 mb-7">
              The complete Grid platform ready for your VPS. Manage unlimited servers, applications, and HA databases with zero restrictions.
            </p>

            <Link
              href="/register"
              className="inline-flex items-center justify-center w-full min-h-[48px] py-3.5 px-6 rounded-xl font-bold text-base bg-emerald-700 text-white hover:bg-emerald-800 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Install Grid Now
            </Link>

            <div className="mt-8 pt-7 border-t border-border">
              <ul className="space-y-3.5">
                {[
                  'Unlimited Managed Servers',
                  'Unlimited Applications & Databases',
                  'Multi-Region WireGuard VPN Mesh',
                  'Predictive Autoscaling & Self-Healing',
                  '40+ One-Click Infrastructure Addons',
                ].map((feature) => (
                  <li key={feature} className="flex items-start gap-3 text-base text-slate-700 dark:text-slate-300">
                    <Check className="w-5 h-5 text-emerald-600 dark:text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="py-20 px-4 border-t border-border">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl font-extrabold text-center text-slate-900 dark:text-white mb-10">
            Frequently Asked Questions
          </h2>
          <PricingFaq faqs={faqs} />
        </div>
      </section>
    </div>
  );
}
