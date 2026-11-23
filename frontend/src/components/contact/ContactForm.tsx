'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Send, ArrowRight, Loader2, AlertCircle } from 'lucide-react';
import api from '@/lib/api';

export function ContactForm() {
  const [form, setForm] = useState({ name: '', email: '', company: '', message: '' });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError('');
    setSubmitting(true);

    try {
      await api.post('/contact/', {
        name: form.name.trim(),
        email: form.email.trim(),
        company: form.company.trim(),
        message: form.message.trim(),
      });
      setSubmitted(true);
    } catch (err: unknown) {
      const eObj = err as { response?: { status?: number; data?: { detail?: string; error?: string } } };
      if (eObj?.response?.status === 429) {
        setError('Rate limit reached. Please wait a bit before sending another message.');
      } else {
        setError(
          eObj?.response?.data?.detail ||
            eObj?.response?.data?.error ||
            'Could not send message. Please email sales@Trulay.co directly.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="rounded-2xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/30 p-8 sm:p-12 text-center"
      >
        <div className="w-14 h-14 rounded-full bg-emerald-100 dark:bg-emerald-900/50 flex items-center justify-center mx-auto mb-5">
          <Send className="w-7 h-7 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
        </div>
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white mb-2">Message Sent</h2>
        <p className="text-slate-600 dark:text-slate-300 mb-6">
          We&apos;ll get back to you within 24 hours.
        </p>
        <div className="flex flex-wrap justify-center gap-4">
          <button
            type="button"
            onClick={() => {
              setForm({ name: '', email: '', company: '', message: '' });
              setSubmitted(false);
            }}
            className="inline-flex items-center justify-center min-h-[44px] px-5 py-2.5 rounded-xl bg-emerald-700 hover:bg-emerald-800 text-white text-sm font-semibold transition-colors"
          >
            Send Another Message
          </button>
          <Link
            href="/"
            className="inline-flex items-center justify-center min-h-[44px] px-5 py-2.5 text-sm font-semibold text-emerald-700 dark:text-emerald-400 hover:underline"
          >
            Back to Home
          </Link>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-2xl border border-border bg-card p-6 sm:p-8 space-y-5">
      <div className="grid sm:grid-cols-2 gap-5">
        <div>
          <label htmlFor="contact-name" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
            Full Name
          </label>
          <input
            id="contact-name"
            type="text"
            required
            autoComplete="name"
            disabled={submitting}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full px-4 py-3 rounded-xl border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
            placeholder="Alex Rivera"
          />
        </div>
        <div>
          <label htmlFor="contact-email" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
            Work Email
          </label>
          <input
            id="contact-email"
            type="email"
            required
            autoComplete="email"
            disabled={submitting}
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            className="w-full px-4 py-3 rounded-xl border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
            placeholder="alex@company.com"
          />
        </div>
      </div>

      <div>
        <label htmlFor="contact-company" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
          Company <span className="font-normal text-muted-foreground">(optional)</span>
        </label>
        <input
          id="contact-company"
          type="text"
          autoComplete="organization"
          disabled={submitting}
          value={form.company}
          onChange={(e) => setForm({ ...form, company: e.target.value })}
          className="w-full px-4 py-3 rounded-xl border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
          placeholder="Your Organization"
        />
      </div>

      <div>
        <label htmlFor="contact-message" className="block text-sm font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
          How can we help?
        </label>
        <textarea
          id="contact-message"
          required
          rows={5}
          disabled={submitting}
          value={form.message}
          onChange={(e) => setForm({ ...form, message: e.target.value })}
          className="w-full px-4 py-3 rounded-xl border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors resize-none"
          placeholder="Tell us about your workloads, server count, or support requirements..."
        />
      </div>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 text-sm text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 p-3.5 rounded-xl"
        >
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="w-full sm:w-auto min-h-[44px] inline-flex items-center justify-center gap-2 px-8 py-3 text-sm font-bold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-60 rounded-xl transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {submitting ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            Sending...
          </>
        ) : (
          <>
            Send Message <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </>
        )}
      </button>
    </form>
  );
}
