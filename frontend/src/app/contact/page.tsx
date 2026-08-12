'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Mail, MessageCircle, Calendar, Building2, Send, ArrowRight } from 'lucide-react';
import api from '@/lib/api';


export default function ContactPage() {
  const [form, setForm] = useState({ name: '', email: '', company: '', message: '' });
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post('/contact/', form);
    } catch {
      // Best-effort — show success regardless to confirm UX
    }
    setSubmitted(true);
  };

  return (
    <main className="min-h-screen bg-white dark:bg-slate-950">


      {/* Hero */}
      <section className="pt-32 pb-12 px-4 text-center">
        <div className="max-w-4xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 text-sm font-medium rounded-full mb-6">
            <Building2 className="w-3.5 h-3.5" />
            Talk to Us
          </div>
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6">
            Let&apos;s Build Together
          </h1>
          <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
            Whether you need enterprise pricing, custom solutions, or just have a question — we&apos;re here.
          </p>
        </div>
      </section>

      <section className="pb-24 px-4">
        <div className="max-w-6xl mx-auto grid lg:grid-cols-5 gap-12">

          {/* Contact Form */}
          <div className="lg:col-span-3">
            {submitted ? (
              <div className="rounded-2xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/30 p-12 text-center">
                <div className="w-16 h-16 rounded-full bg-emerald-100 dark:bg-emerald-900/50 flex items-center justify-center mx-auto mb-6">
                  <Send className="w-8 h-8 text-emerald-600" />
                </div>
                <h2 className="text-2xl font-bold text-slate-900 dark:text-white mb-3">Message Sent</h2>
                <p className="text-slate-600 dark:text-slate-400 mb-6">We&apos;ll get back to you within 24 hours.</p>
                <Link href="/" className="text-emerald-600 dark:text-emerald-400 font-semibold hover:underline">
                  Back to Home
                </Link>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="grid sm:grid-cols-2 gap-5">
                  <div>
                    <label htmlFor="name" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">Full Name</label>
                    <input
                      id="name" type="text" required
                      value={form.name} onChange={e => setForm({...form, name: e.target.value})}
                      className="w-full px-4 py-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all"
                      placeholder="John Doe"
                    />
                  </div>
                  <div>
                    <label htmlFor="email" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">Work Email</label>
                    <input
                      id="email" type="email" required
                      value={form.email} onChange={e => setForm({...form, email: e.target.value})}
                      className="w-full px-4 py-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all"
                      placeholder="john@company.com"
                    />
                  </div>
                </div>
                <div>
                  <label htmlFor="company" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">Company</label>
                  <input
                    id="company" type="text"
                    value={form.company} onChange={e => setForm({...form, company: e.target.value})}
                    className="w-full px-4 py-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all"
                    placeholder="Acme Inc."
                  />
                </div>
                <div>
                  <label htmlFor="message" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">How can we help?</label>
                  <textarea
                    id="message" required rows={5}
                    value={form.message} onChange={e => setForm({...form, message: e.target.value})}
                    className="w-full px-4 py-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all resize-none"
                    placeholder="Tell us about your project, team size, and requirements..."
                  />
                </div>
                <button
                  type="submit"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-8 py-3 text-sm font-bold text-white bg-emerald-600 rounded-xl hover:bg-emerald-700 transition-all shadow-lg shadow-emerald-600/30"
                >
                  Send Message <ArrowRight className="w-4 h-4" />
                </button>
              </form>
            )}
          </div>

          {/* Sidebar */}
          <div className="lg:col-span-2 space-y-6">
            <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-6">
              <h3 className="font-bold text-slate-900 dark:text-white mb-4">Other Ways to Reach Us</h3>
              <div className="space-y-4">
                <a href="mailto:sales@Trulay.co" className="flex items-center gap-3 text-sm text-slate-600 dark:text-slate-400 hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">
                  <Mail className="w-5 h-5" />
                  sales@Trulay.co
                </a>
                <a href="mailto:community@Trulay.co?subject=Community%20Access" className="flex items-center gap-3 text-sm text-slate-600 dark:text-slate-400 hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">
                  <MessageCircle className="w-5 h-5" />
                  Discord Community
                </a>
                <a href="mailto:sales@Trulay.co?subject=Demo%20Request" className="flex items-center gap-3 text-sm text-slate-600 dark:text-slate-400 hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">
                  <Calendar className="w-5 h-5" />
                  Schedule a Demo
                </a>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-6">
              <h3 className="font-bold text-slate-900 dark:text-white mb-3">Enterprise Ready</h3>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
                Custom SLAs, on-premise deployment, HIPAA/SOC2 compliance, and dedicated support engineers.
              </p>
              <Link href="/pricing" className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 hover:underline inline-flex items-center gap-1">
                View Plans <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
          </div>

        </div>
      </section>
    </main>
  );
}
