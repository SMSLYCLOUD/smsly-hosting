'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Check, X, Zap, Shield, Users, Building2, ChevronDown } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';

const tiers = [
  {
    name: 'Community',
    price: 'Free',
    period: 'forever',
    description: 'Perfect for personal projects and learning.',
    color: 'from-slate-500 to-slate-600',
    cta: 'Get Started',
    ctaLink: '/register',
    highlighted: false,
    icon: Zap,
    features: [
      'Single server deployment',
      '3 applications',
      'Git-based deploys',
      'Auto-detection (20+ frameworks)',
      'Community support',
      'Basic monitoring',
      'Shared build cache',
    ],
  },
  {
    name: 'Pro',
    price: '$29',
    period: '/mo per server',
    description: 'For serious developers and growing projects.',
    color: 'from-emerald-500 to-green-600',
    cta: 'Start Pro Trial',
    ctaLink: '/register?plan=pro',
    highlighted: true,
    icon: Shield,
    features: [
      'Everything in Community',
      'Unlimited applications',
      'Predictive autoscaler',
      'Anomaly detection',
      'Self-healing containers',
      'AI deploy advisor',
      'SSL certificates (auto)',
      'Priority email support',
      'Custom domains',
      'Preview environments',
    ],
  },
  {
    name: 'Team',
    price: '$79',
    period: '/mo per server',
    description: 'For teams that need collaboration and compliance.',
    color: 'from-violet-500 to-purple-600',
    cta: 'Start Team Trial',
    ctaLink: '/register?plan=team',
    highlighted: false,
    icon: Users,
    features: [
      'Everything in Pro',
      'RBAC & team management',
      'Audit logs',
      'SOC 2 compliance template',
      'Distributed tracing',
      'Database branching',
      'Deploy approvals workflow',
      'Slack/Discord integration',
      'Cost allocation reports',
      '3D topology view',
    ],
  },
  {
    name: 'Enterprise',
    price: 'Custom',
    period: '',
    description: 'For organizations with mission-critical workloads.',
    color: 'from-amber-500 to-orange-600',
    cta: 'Talk to Sales',
    ctaLink: '/contact',
    highlighted: false,
    icon: Building2,
    features: [
      'Everything in Team',
      'Multi-cloud fabric',
      'Bare metal scheduler',
      'Chaos engineering suite',
      'Zero-knowledge deployments',
      'Dedicated support engineer',
      'Custom SLA (99.99%)',
      'On-premise deployment',
      'HIPAA & PCI-DSS compliance',
      'SSO / SAML integration',
    ],
  },
];

const comparisonRows = [
  { feature: 'Applications', community: '3', pro: 'Unlimited', team: 'Unlimited', enterprise: 'Unlimited' },
  { feature: 'Team members', community: '1', pro: '3', team: 'Unlimited', enterprise: 'Unlimited' },
  { feature: 'Custom domains', community: false, pro: true, team: true, enterprise: true },
  { feature: 'SSL certificates', community: false, pro: true, team: true, enterprise: true },
  { feature: 'AI autoscaler', community: false, pro: true, team: true, enterprise: true },
  { feature: 'Self-healing', community: false, pro: true, team: true, enterprise: true },
  { feature: 'Audit logs', community: false, pro: false, team: true, enterprise: true },
  { feature: 'RBAC', community: false, pro: false, team: true, enterprise: true },
  { feature: 'Deploy approvals', community: false, pro: false, team: true, enterprise: true },
  { feature: 'SOC 2 / HIPAA', community: false, pro: false, team: 'SOC 2', enterprise: 'All' },
  { feature: 'Dedicated support', community: false, pro: false, team: false, enterprise: true },
  { feature: 'SLA', community: 'Best effort', pro: '99.9%', team: '99.95%', enterprise: '99.99%' },
];

const faqs = [
  {
    q: 'Can I switch plans at any time?',
    a: 'Yes. Upgrade instantly, downgrade at the end of your billing cycle. No lock-in contracts.',
  },
  {
    q: 'What counts as a "server"?',
    a: 'A server is any VPS, dedicated server, or cloud instance where you install the SMSLY agent. Each server needs its own license.',
  },
  {
    q: 'Is there a free trial for paid plans?',
    a: 'Yes. Pro and Team plans include a 14-day free trial with full access to all features. No credit card required.',
  },
  {
    q: 'What happens if I exceed my application limit?',
    a: 'On the Community plan, you\'ll be prompted to upgrade. We never delete your applications — you just won\'t be able to create new ones.',
  },
  {
    q: 'Do you offer discounts for annual billing?',
    a: 'Yes. Annual billing saves 20% compared to monthly. Contact sales for custom enterprise pricing.',
  },
];

export default function PricingPage() {
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">
      <Navbar />

      {/* Hero */}
      <section className="pt-32 pb-12 px-4 text-center">
        <div className="max-w-4xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 text-sm font-medium rounded-full mb-6">
            <Zap className="w-3.5 h-3.5" />
            Simple, transparent pricing
          </div>
          <h1 className="text-4xl md:text-6xl font-extrabold text-slate-900 dark:text-white mb-6">
            Start Free. Scale as You Grow.
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
            No surprises. No hidden fees. Every plan includes core deployment features.
          </p>
        </div>
      </section>

      {/* Pricing Cards */}
      <section className="pb-24 px-4">
        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {tiers.map((tier) => (
            <div
              key={tier.name}
              className={`relative rounded-2xl p-6 border transition-all duration-300 flex flex-col ${
                tier.highlighted
                  ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30 shadow-xl shadow-emerald-500/10 scale-[1.02] ring-2 ring-emerald-500/20'
                  : 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-emerald-300 dark:hover:border-emerald-700 hover:shadow-lg'
              }`}
            >
              {tier.highlighted && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-4 py-1 bg-emerald-600 text-white text-xs font-bold rounded-full uppercase tracking-wider">
                  Most Popular
                </div>
              )}

              <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${tier.color} flex items-center justify-center mb-4`}>
                <tier.icon className="w-6 h-6 text-white" />
              </div>

              <h3 className="text-xl font-bold text-slate-900 dark:text-white">{tier.name}</h3>
              <div className="mt-2 mb-1">
                <span className="text-4xl font-extrabold text-slate-900 dark:text-white">{tier.price}</span>
                {tier.period && <span className="text-slate-500 dark:text-slate-400 text-sm ml-1">{tier.period}</span>}
              </div>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-6">{tier.description}</p>

              <Link
                href={tier.ctaLink}
                className={`block w-full text-center py-3 px-4 rounded-xl font-bold text-sm transition-all ${
                  tier.highlighted
                    ? 'bg-emerald-600 text-white hover:bg-emerald-700 shadow-lg shadow-emerald-600/30'
                    : 'bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-white hover:bg-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                {tier.cta}
              </Link>

              <div className="mt-6 pt-6 border-t border-slate-200 dark:border-slate-800 flex-1">
                <ul className="space-y-3">
                  {tier.features.map((feature) => (
                    <li key={feature} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                      <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
                      {feature}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Comparison Table */}
      <section className="py-24 bg-slate-50 dark:bg-slate-900">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="text-3xl font-extrabold text-center text-slate-900 dark:text-white mb-12">
            Compare Plans
          </h2>
          <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-700 shadow-lg">
            <table className="w-full text-left border-collapse bg-white dark:bg-slate-800">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="px-6 py-4 text-sm font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Feature</th>
                  <th className="px-6 py-4 text-sm font-semibold text-slate-500 dark:text-slate-400 text-center">Community</th>
                  <th className="px-6 py-4 text-sm font-semibold text-emerald-600 dark:text-emerald-400 text-center">Pro</th>
                  <th className="px-6 py-4 text-sm font-semibold text-slate-500 dark:text-slate-400 text-center">Team</th>
                  <th className="px-6 py-4 text-sm font-semibold text-slate-500 dark:text-slate-400 text-center">Enterprise</th>
                </tr>
              </thead>
              <tbody>
                {comparisonRows.map((row, i) => (
                  <tr key={i} className="border-b border-slate-100 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors">
                    <td className="px-6 py-4 text-sm font-medium text-slate-900 dark:text-white">{row.feature}</td>
                    {(['community', 'pro', 'team', 'enterprise'] as const).map((plan) => {
                      const val = row[plan];
                      return (
                        <td key={plan} className="px-6 py-4 text-center">
                          {val === true ? (
                            <Check className="w-5 h-5 text-emerald-500 mx-auto" />
                          ) : val === false ? (
                            <X className="w-5 h-5 text-slate-300 dark:text-slate-600 mx-auto" />
                          ) : (
                            <span className="text-sm text-slate-700 dark:text-slate-300">{val}</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
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

      {/* CTA */}
      <section className="py-16 bg-gradient-to-r from-emerald-600 via-green-600 to-teal-600 text-white">
        <div className="max-w-4xl mx-auto px-4 text-center">
          <h2 className="text-3xl font-extrabold mb-4">Ready to Get Started?</h2>
          <p className="text-lg text-white/80 mb-8">Start free. No credit card required. Upgrade when you need to.</p>
          <Link
            href="/register"
            className="inline-flex items-center gap-2 px-8 py-4 text-lg font-bold text-emerald-600 bg-white rounded-xl hover:bg-slate-100 transition-all shadow-lg"
          >
            Deploy Your First App
          </Link>
        </div>
      </section>
    </main>
  );
}
