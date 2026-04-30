'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Check, X, Zap, Shield, Users, Building2, ChevronDown, Loader2 } from 'lucide-react';
import { billingApi, PricingPlan } from '@/lib/api';

const faqs = [
  {
    q: 'Is CloudNeuron really free?',
    a: 'Yes. CloudNeuron is 100% open source and free to use. You deploy it on your own infrastructure — you only pay your cloud provider for the server itself.',
  },
  {
    q: 'What counts as a "server"?',
    a: 'A server is any VPS, dedicated server, or cloud instance where you install CloudNeuron. There are no limits on how many servers you can manage.',
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
    a: 'Absolutely! CloudNeuron is open source and we welcome contributions. Check out our GitHub repository to get started.',
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
            Simple, transparent pricing
          </div>
          <h1 className="text-4xl md:text-6xl font-extrabold text-slate-900 dark:text-white mb-6">
            100% Open Source. 100% Free.
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
            No surprises. No hidden fees. CloudNeuron is fully open source — deploy on your own infrastructure, forever free.
          </p>

          {/* Billing Toggle */}
          <div className="flex items-center justify-center mt-8 gap-4">
              <span className={`text-sm font-medium ${billingCycle === 'MONTHLY' ? 'text-slate-900 dark:text-white' : 'text-slate-500'}`}>Monthly</span>
              <button
                  onClick={() => setBillingCycle(c => c === 'MONTHLY' ? 'YEARLY' : 'MONTHLY')}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                      billingCycle === 'YEARLY' ? 'bg-emerald-500' : 'bg-slate-200 dark:bg-slate-700'
                  }`}
              >
                  <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
                      billingCycle === 'YEARLY' ? 'translate-x-6' : 'translate-x-1'
                  }`} />
              </button>
              <span className={`text-sm font-medium ${billingCycle === 'YEARLY' ? 'text-slate-900 dark:text-white' : 'text-slate-500'}`}>
                  Yearly <span className="text-emerald-500 text-xs font-bold ml-1">(Save 20%)</span>
              </span>
          </div>
        </div>
      </section>

      {/* Pricing Cards */}
      <section className="pb-24 px-4">
        {loading ? (
             <div className="flex justify-center py-12">
                <Loader2 className="w-8 h-8 animate-spin text-emerald-500" />
             </div>
        ) : plans.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
                No pricing plans available. Please check back later.
            </div>
        ) : (
            <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {plans.map((tier) => {
                const Icon = getIcon(tier.slug);
                const color = getColor(tier.slug);
                const price = billingCycle === 'MONTHLY' ? tier.price_monthly_usd : tier.price_yearly_usd;
                const period = billingCycle === 'MONTHLY' ? '/mo' : '/yr';
                const highlighted = tier.slug.includes('pro');

                return (
                <div
                key={tier.id}
                className={`relative rounded-2xl p-6 border transition-all duration-300 flex flex-col ${
                    highlighted
                    ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30 shadow-xl shadow-emerald-500/10 scale-[1.02] ring-2 ring-emerald-500/20'
                    : 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-emerald-300 dark:hover:border-emerald-700 hover:shadow-lg'
                }`}
                >
                {highlighted && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-4 py-1 bg-emerald-600 text-white text-xs font-bold rounded-full uppercase tracking-wider">
                    Most Popular
                    </div>
                )}

                <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${color} flex items-center justify-center mb-4`}>
                    <Icon className="w-6 h-6 text-white" />
                </div>

                <h3 className="text-xl font-bold text-slate-900 dark:text-white">{tier.name}</h3>
                <div className="mt-2 mb-1">
                    <span className="text-4xl font-extrabold text-slate-900 dark:text-white">${price}</span>
                    <span className="text-slate-500 dark:text-slate-400 text-sm ml-1">{period}</span>
                </div>
                <p className="text-sm text-slate-600 dark:text-slate-400 mb-6">{tier.description}</p>

                <Link
                    href={`/register?plan=${tier.slug}`}
                    className={`block w-full text-center py-3 px-4 rounded-xl font-bold text-sm transition-all ${
                    highlighted
                        ? 'bg-emerald-600 text-white hover:bg-emerald-700 shadow-lg shadow-emerald-600/30'
                        : 'bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-white hover:bg-slate-200 dark:hover:bg-slate-700'
                    }`}
                >
                    Get Started
                </Link>

                <div className="mt-6 pt-6 border-t border-slate-200 dark:border-slate-800 flex-1">
                    <ul className="space-y-3">
                        <li className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                            <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
                            {tier.max_services} Services
                        </li>
                        <li className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                            <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
                            {tier.max_cpu_cores} vCPU Limit
                        </li>
                        <li className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                            <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
                            {tier.max_memory_mb} MB RAM Limit
                        </li>
                        <li className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                            {tier.features.has_auto_scaling ? (
                                <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
                            ) : (
                                <X className="w-4 h-4 text-slate-400 mt-0.5 flex-shrink-0" />
                            )}
                            Auto-scaling
                        </li>
                        <li className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                            {tier.features.has_backup ? (
                                <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
                            ) : (
                                <X className="w-4 h-4 text-slate-400 mt-0.5 flex-shrink-0" />
                            )}
                            Backups
                        </li>
                    </ul>
                </div>
                </div>
            )})}
            </div>
        )}
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
