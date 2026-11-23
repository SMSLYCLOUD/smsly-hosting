import type { Metadata } from 'next';
import Link from 'next/link';
import { Mail, MessageCircle, Calendar, Building2, ArrowRight } from 'lucide-react';
import { ContactForm } from '@/components/contact/ContactForm';

export const metadata: Metadata = {
  title: 'Contact Us',
  description:
    'Contact the Trulay Grid engineering team for enterprise SLAs, custom deployment architectures, or platform support.',
  alternates: { canonical: '/contact' },
};

export default function ContactPage() {
  return (
    <div className="bg-slate-50 dark:bg-slate-950">
      {/* Hero */}
      <section className="pt-28 pb-12 px-4 text-center">
        <div className="max-w-4xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-800 dark:text-emerald-300 text-sm font-semibold rounded-full mb-6">
            <Building2 className="w-3.5 h-3.5" aria-hidden="true" />
            Talk to Us
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6">
            Let&apos;s Build Together
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-300 max-w-2xl mx-auto">
            Whether you need custom infrastructure guidance, enterprise SLAs, or just have a question—we&apos;re here.
          </p>
        </div>
      </section>

      <section className="pb-24 px-4">
        <div className="max-w-6xl mx-auto grid lg:grid-cols-5 gap-10">
          <div className="lg:col-span-3">
            <ContactForm />
          </div>

          {/* Sidebar */}
          <div className="lg:col-span-2 space-y-6">
            <div className="rounded-2xl border border-border bg-card p-6">
              <h2 className="text-base font-bold text-slate-900 dark:text-white mb-3">Other Ways to Reach Us</h2>
              <div className="space-y-1">
                <a
                  href="mailto:sales@Trulay.co"
                  className="flex items-center gap-3 min-h-[44px] py-2 text-sm text-slate-700 dark:text-slate-300 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
                >
                  <Mail className="w-5 h-5 shrink-0" aria-hidden="true" />
                  <span>sales@Trulay.co</span>
                </a>
                <a
                  href="mailto:community@Trulay.co?subject=Community%20Access"
                  className="flex items-center gap-3 min-h-[44px] py-2 text-sm text-slate-700 dark:text-slate-300 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
                >
                  <MessageCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
                  <span>Email Community Team</span>
                </a>
                <a
                  href="mailto:sales@Trulay.co?subject=Demo%20Request"
                  className="flex items-center gap-3 min-h-[44px] py-2 text-sm text-slate-700 dark:text-slate-300 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
                >
                  <Calendar className="w-5 h-5 shrink-0" aria-hidden="true" />
                  <span>Request a Demo (Email)</span>
                </a>
              </div>
            </div>

            <div className="rounded-2xl border border-border bg-card p-6">
              <h2 className="text-base font-bold text-slate-900 dark:text-white mb-2">Enterprise Ready</h2>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4 leading-relaxed">
                Custom SLAs, dedicated onboarding, air-gapped setup assistance, and architecture reviews for production clusters.
              </p>
              <Link
                href="/pricing"
                className="inline-flex items-center gap-1 min-h-[44px] text-sm font-semibold text-emerald-700 dark:text-emerald-400 hover:underline"
              >
                View Edition Details <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
