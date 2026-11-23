'use client';

import { useState } from 'react';
import { Copy, Check } from 'lucide-react';

export function CodeBlock({
  children,
  lang = 'bash',
}: {
  children: string;
  lang?: string;
}) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(children.trim());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative group my-4" data-lang={lang}>
      <pre className="bg-slate-900 dark:bg-slate-900/90 text-slate-100 p-4 pr-14 rounded-xl overflow-x-auto text-sm leading-relaxed font-mono border border-slate-800 dark:border-slate-700/60">
        <code>{children.trim()}</code>
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Copied to clipboard' : 'Copy to clipboard'}
        className="absolute top-2.5 right-2.5 inline-flex items-center justify-center min-w-[36px] min-h-[36px] p-2 rounded-lg bg-slate-800/90 hover:bg-slate-700 text-slate-200 opacity-80 sm:opacity-0 sm:group-hover:opacity-100 focus-visible:opacity-100 group-focus-within:opacity-100 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
      >
        {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      </button>
    </div>
  );
}
