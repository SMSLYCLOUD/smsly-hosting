'use client';

import { useEffect, useState } from 'react';
import { ExternalLink } from 'lucide-react';

export interface TocItem {
  id: string;
  label: string;
}

const activeClasses: Record<string, string> = {
  emerald: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-800 dark:text-emerald-300 font-semibold',
  purple: 'bg-purple-50 dark:bg-purple-900/30 text-purple-800 dark:text-purple-300 font-semibold',
  indigo: 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-800 dark:text-indigo-300 font-semibold',
  violet: 'bg-violet-50 dark:bg-violet-900/30 text-violet-800 dark:text-violet-300 font-semibold',
  blue: 'bg-blue-50 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300 font-semibold',
  cyan: 'bg-cyan-50 dark:bg-cyan-900/30 text-cyan-800 dark:text-cyan-300 font-semibold',
  orange: 'bg-orange-50 dark:bg-orange-900/30 text-orange-800 dark:text-orange-300 font-semibold',
};

export function DocsToc({
  items,
  accent = 'emerald',
  githubHref,
}: {
  items: TocItem[];
  accent?: keyof typeof activeClasses;
  githubHref?: string;
}) {
  const [activeSection, setActiveSection] = useState('');

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.find((e) => e.isIntersecting);
        if (visible) setActiveSection(visible.target.id);
      },
      { rootMargin: '-80px 0px -70% 0px' },
    );
    items.forEach((item) => {
      const el = document.getElementById(item.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [items]);

  const activeStyle = activeClasses[accent] || activeClasses.emerald;

  return (
    <aside className="hidden lg:block w-56 flex-shrink-0">
      <nav aria-label="On this page" className="sticky top-24 space-y-0.5">
        <p className="text-xs font-bold text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-3 px-3">
          On this page
        </p>
        {items.map((item) => (
          <a
            key={item.id}
            href={`#${item.id}`}
            aria-current={activeSection === item.id ? 'location' : undefined}
            className={`flex items-center px-3 py-2 rounded-lg text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
              activeSection === item.id
                ? activeStyle
                : 'text-slate-700 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-900'
            }`}
          >
            <span className="truncate">{item.label}</span>
          </a>
        ))}

        {githubHref && (
          <div className="pt-4 mt-4 border-t border-slate-200 dark:border-slate-800">
            <a
              href={githubHref}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-2 text-xs text-slate-600 dark:text-slate-400 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
            >
              <ExternalLink size={12} aria-hidden="true" />
              <span>View on GitHub</span>
            </a>
          </div>
        )}
      </nav>
    </aside>
  );
}
