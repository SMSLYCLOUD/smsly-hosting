'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Check, X, Zap, Shield, Users, Building2, ChevronDown, Loader2 } from 'lucide-react';
import { billingApi, PricingPlan } from '@/lib/api';

const faqs = [
  {
    q: 'Is Grid really free?',
    a: 'Yes. Grid is 100% open source and free to use. You deploy it on your own infrastructure — you only pay your cloud provider for the server itself.',
  },
  {
    q: 'What counts as a "server"?',
    a: 'A server is any VPS, dedicated server, or cloud instance where you install Grid. There are no limits on how many servers you can manage.',
  },
  {
    q: 'Is there a catch? What about paid plans?',
    a: 'No catch. Every feature is included for free. We may offer optional managed hosting and enterprise support in the future, but the core platform will always be free and open source.',
  },
  {
    q: 'What happens if I exceed my application limit?',
    a: 'There are no artificial application limits. Deploy as many applications as your server hardware can handle.',
  },
  {
    q: 'Can I contribute to the project?',
    a: 'Absolutely! Grid is open source and we welcome contributions. Check out our GitHub repository to get started.',
  },
];

export default function PricingPage() {
  const [plans, setPlans] = useState<PricingPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [billingCycle, setBillingCycle] = useState<'MONTHLY' | 'YEARLY'>('MONTHLY');
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  useEffect(() => {
    async function loadPlans() {
      try {
        const data = await billingApi.getPlans();
        setPlans(data);
      } catch (err) {
        console.error('Failed to load plans:', err);
      } finally {
        setLoading(false);
      }
    }
    loadPlans();
  }, []);

  const getIcon = (slug: string) => {
    if (slug.includes('pro')) return Shield;
    if (slug.includes('team') || slug.includes('enterprise')) return Users;
    if (slug.includes('corp')) return Building2;
    return Zap;
  };

  const getColor = (slug: string) => {
    if (slug.includes('pro')) return 'from-emerald-500 to-green-600';
    if (slug.includes('team')) return 'from-violet-500 to-purple-600';
    if (slug.includes('enterprise')) return 'from-amber-500 to-orange-600';
    return 'from-slate-500 to-slate-600';
  };

  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      {/* Hero */}
      <section className="pt-32 pb-12 px-4 text-center">
        <div className="max-w-4xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 text-sm font-medium rounded-full mb-6">
            <Zap className="w-3.5 h-3.5" />
          </div>
          <h1 className="text-4xl md:text-6xl font-extrabold text-slate-900 dark:text-white mb-6">
            100% Open Source. 100% Free.
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
            Grid is a self-hosted control plane. You deploy it on your own hardware, and you keep 100% of the control. No subscriptions, no hidden fees, no &quot;Cloud Tax.&quot;
          </p>
        </div>
      </section>

      {/* Pricing Cards */}
      <section className="pb-24 px-4">
            <div className="max-w-4xl mx-auto flex justify-center">
                <div className="relative rounded-2xl p-8 border border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30 shadow-xl shadow-emerald-500/10 scale-[1.05] ring-2 ring-emerald-500/20 max-w-lg w-full flex flex-col">
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-4 py-1 bg-emerald-600 text-white text-xs font-bold rounded-full uppercase tracking-wider">
                        Free Forever
                    </div>

                    <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-emerald-500 to-green-600 flex items-center justify-center mb-6">
                        <Shield className="w-7 h-7 text-white" />
                    </div>

                    <h3 className="text-2xl font-bold text-slate-900 dark:text-white">Community Edition</h3>
                    <div className="mt-2 mb-4">
                        <span className="text-5xl font-extrabold text-slate-900 dark:text-white">$0</span>
                        <span className="text-slate-500 dark:text-slate-400 text-sm ml-1">/ forever</span>
                    </div>
                    <p className="text-base text-slate-600 dark:text-slate-400 mb-8">
                        The complete Grid platform, open-sourced and ready for your VPS. Manage unlimited servers, applications, and clusters with zero restrictions.
                    </p>

                    <Link
                        href="/register"
                        className="block w-full text-center py-4 px-6 rounded-xl font-bold text-base bg-emerald-600 text-white hover:bg-emerald-700 shadow-lg shadow-emerald-600/30 transition-all transform hover:scale-105"
                    >
                        Install Grid Now
                    </Link>

                    <div className="mt-8 pt-8 border-t border-slate-200 dark:border-slate-800">
                        <ul className="space-y-4">
                            <li className="flex items-start gap-3 text-base text-slate-700 dark:text-slate-300">
                                <Check className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                                Unlimited Managed Servers
                            </li>
                            <li className="flex items-start gap-3 text-base text-slate-700 dark:text-slate-300">
                                <Check className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                                Unlimited Applications & Databases
                            </li>
                            <li className="flex items-start gap-3 text-base text-slate-700 dark:text-slate-300">
                                <Check className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                                Multi-Region VPN Mesh
                            </li>
                            <li className="flex items-start gap-3 text-base text-slate-700 dark:text-slate-300">
                                <Check className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                                Predictive Autoscaling
                            </li>
                            <li className="flex items-start gap-3 text-base text-slate-700 dark:text-slate-300">
                                <Check className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" />
                                AI-Driven Orchestration
                            </li>
                        </ul>
                    </div>
                </div>
            </div>
      </section>

      {/* FAQ */}
      <section className="py-24 px-4">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl font-extrabold text-center text-slate-900 dark:text-white mb-12">
            Frequently Asked Questions
          </h2>
          <div className="space-y-3">
            {faqs.map((faq, i) => (
              <div
                key={i}
                className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 overflow-hidden"
              >
                <button
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors"
                >
                  <span className="font-semibold text-slate-900 dark:text-white">{faq.q}</span>
                  <ChevronDown className={`w-5 h-5 text-slate-400 transition-transform ${openFaq === i ? 'rotate-180' : ''}`} />
                </button>
                {openFaq === i && (
                  <div className="px-6 pb-4 text-slate-600 dark:text-slate-400">
                    {faq.a}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
