'use client';

import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

interface FaqItem {
  q: string;
  a: string;
}

export function PricingFaq({ faqs }: { faqs: FaqItem[] }) {
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  return (
    <div className="space-y-3">
      {faqs.map((faq, i) => {
        const isOpen = openFaq === i;
        const panelId = `pricing-faq-panel-${i}`;
        const buttonId = `pricing-faq-btn-${i}`;
        return (
          <div
            key={faq.q}
            className="rounded-xl border border-slate-200 dark:border-slate-800 bg-card overflow-hidden"
          >
            <button
              id={buttonId}
              type="button"
              aria-expanded={isOpen}
              aria-controls={panelId}
              onClick={() => setOpenFaq(isOpen ? null : i)}
              className="w-full min-h-[48px] flex items-center justify-between gap-4 px-6 py-4 text-left hover:bg-muted/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="font-semibold text-slate-900 dark:text-white">{faq.q}</span>
              <ChevronDown
                className={`w-5 h-5 shrink-0 text-slate-600 dark:text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                aria-hidden="true"
              />
            </button>
            {isOpen && (
              <div
                id={panelId}
                role="region"
                aria-labelledby={buttonId}
                className="px-6 pb-4 text-slate-600 dark:text-slate-300 text-sm leading-relaxed"
              >
                {faq.a}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
