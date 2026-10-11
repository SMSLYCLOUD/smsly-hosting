'use client';

import { useState, useEffect } from 'react';
import { MessageSquare, Fingerprint, TrendingUp, Megaphone, ArrowUpRight } from 'lucide-react';
import { m, AnimatePresence } from 'framer-motion';
import { systemApi, PromoItem } from '@/lib/api';

const UTM = 'utm_source=smsly-grid&utm_medium=banner&utm_campaign=cross-sell';

const DEFAULT_PRODUCTS: PromoItem[] = [
    {
        id: 'sms-apis',
        title: 'Trulay Communication APIs',
        description: 'SMS, WhatsApp, Voice, Email — global messaging for your apps.',
        href: `https://Trulay.co?${UTM}&utm_content=sms-apis`,
        cta: 'Get API keys',
        color: 'text-blue-500',
        bg: 'bg-blue-500/10',
    },
    {
        id: 'identity',
        title: 'Trulay Identity & Trust',
        description: 'SilentOTP, verification, abuse prevention, and media integrity.',
        href: `https://Trulay.co?${UTM}&utm_content=identity`,
        cta: 'Secure your app',
        color: 'text-emerald-500',
        bg: 'bg-emerald-500/10',
    },
    {
        id: 'ignite',
        title: 'Ignite Growth Automation',
        description: 'AI-assisted marketing planning, publishing, lead gen, and analytics.',
        href: `https://Trulay.co?${UTM}&utm_content=ignite`,
        cta: 'Grow faster',
        color: 'text-amber-500',
        bg: 'bg-amber-500/10',
    },
];

const ICONS: Record<string, typeof MessageSquare> = {
    'sms-apis': MessageSquare,
    identity: Fingerprint,
    ignite: TrendingUp,
};

function iconFor(id: string) {
    return ICONS[id] ?? Megaphone;
}

/**
 * Server-driven cross-sell banner. Admin kill-switch (PROMOS_ENABLED) wins;
 * empty server items fall back to built-in defaults. Legacy backends without
 * promo keys keep the old always-on defaults; fetch errors hide (fail-closed).
 */
export function SmslyCrossSell() {
    const [products, setProducts] = useState<PromoItem[] | null>(null);
    const [rotateMs, setRotateMs] = useState(8000);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [paused, setPaused] = useState(false);
    const [reducedMotion, setReducedMotion] = useState(false);

    useEffect(() => {
        let cancelled = false;
        systemApi.getPromos().then(
            (cfg) => {
                if (cancelled) return;
                if (cfg.configured && !cfg.enabled) {
                    setProducts([]);
                    return;
                }
                setProducts(cfg.items.length > 0 ? cfg.items : DEFAULT_PRODUCTS);
                setRotateMs(cfg.rotateSeconds * 1000);
            },
            () => {
                if (!cancelled) setProducts([]);
            },
        );
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        try {
            const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
            setReducedMotion(mq.matches);
            const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
            mq.addEventListener('change', onChange);
            return () => mq.removeEventListener('change', onChange);
        } catch {
            return;
        }
    }, []);

    useEffect(() => {
        if (paused || reducedMotion || !products || products.length < 2) return;
        const interval = setInterval(() => {
            setCurrentIndex(prev => (prev + 1) % products.length);
        }, rotateMs);
        return () => clearInterval(interval);
    }, [paused, reducedMotion, products, rotateMs]);

    if (!products || products.length === 0) return null;

    const product = products[currentIndex % products.length];
    const Icon = iconFor(product.id);

    return (
        <div
            onMouseEnter={() => setPaused(true)}
            onMouseLeave={() => setPaused(false)}
            onFocus={() => setPaused(true)}
            onBlur={() => setPaused(false)}
        >
            <AnimatePresence mode="wait">
                <m.div
                    key={product.id}
                    initial={reducedMotion ? false : { opacity: 0, y: -6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 6 }}
                    className="relative rounded-xl border border-slate-200 dark:border-slate-700 bg-gradient-to-r from-slate-50 to-white dark:from-slate-900 dark:to-slate-950 p-4 pr-10 flex items-center gap-3"
                >
                    <div className={`p-2.5 rounded-lg ${product.bg || 'bg-blue-500/10'}`}>
                        <Icon className={`w-5 h-5 ${product.color || 'text-blue-500'}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-slate-900 dark:text-white">{product.title}</p>
                        {product.description && (
                            <p className="text-xs text-slate-500 dark:text-slate-400 truncate">{product.description}</p>
                        )}
                    </div>
                    <a
                        href={product.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap"
                    >
                        {product.cta || 'Learn more'} <ArrowUpRight className="w-3 h-3" />
                    </a>
                </m.div>
            </AnimatePresence>
            {products.length > 1 && (
                <div className="flex items-center justify-center gap-1.5 mt-2" role="tablist" aria-label="Promotions">
                    {products.map((p, i) => (
                        <button
                            key={p.id}
                            type="button"
                            role="tab"
                            aria-selected={i === currentIndex % products.length}
                            aria-label={p.title}
                            onClick={() => setCurrentIndex(i)}
                            className={`h-1.5 rounded-full transition-all ${
                                i === currentIndex % products.length ? 'w-5 bg-primary' : 'w-1.5 bg-muted-foreground/30 hover:bg-muted-foreground/60'
                            }`}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
