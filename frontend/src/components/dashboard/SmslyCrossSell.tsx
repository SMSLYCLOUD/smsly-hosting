'use client';

import { useState, useEffect } from 'react';
import { MessageSquare, Fingerprint, TrendingUp, ArrowUpRight } from 'lucide-react';
import { m, AnimatePresence } from 'framer-motion';

const products = [
    {
        id: 'sms-apis',
        title: 'Trulay Communication APIs',
        description: 'SMS, WhatsApp, Voice, Email — global messaging for your apps.',
        icon: MessageSquare,
        color: 'text-blue-500',
        bg: 'bg-blue-500/10',
        href: 'https://Trulay.co',
        cta: 'Get API keys'
    },
    {
        id: 'identity',
        title: 'Trulay Identity & Trust',
        description: 'SilentOTP, verification, abuse prevention, and media integrity.',
        icon: Fingerprint,
        color: 'text-emerald-500',
        bg: 'bg-emerald-500/10',
        href: 'https://Trulay.co',
        cta: 'Secure your app'
    },
    {
        id: 'ignite',
        title: 'Ignite Growth Automation',
        description: 'AI-assisted marketing planning, publishing, lead gen, and analytics.',
        icon: TrendingUp,
        color: 'text-amber-500',
        bg: 'bg-amber-500/10',
        href: 'https://Trulay.co',
        cta: 'Grow faster'
    }
];

const ROTATE_INTERVAL = 8000;

export function SmslyCrossSell() {
    const [currentIndex, setCurrentIndex] = useState(0);

    useEffect(() => {
        const interval = setInterval(() => {
            setCurrentIndex(prev => (prev + 1) % products.length);
        }, ROTATE_INTERVAL);
        return () => clearInterval(interval);
    }, []);

    const product = products[currentIndex];
    const Icon = product.icon;

    return (
        <AnimatePresence mode="wait">
            <m.div
                key={product.id}
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                className="relative rounded-xl border border-slate-200 dark:border-slate-700 bg-gradient-to-r from-slate-50 to-white dark:from-slate-900 dark:to-slate-950 p-4 flex items-center gap-3"
            >
                <div className={`p-2.5 rounded-lg ${product.bg}`}>
                    <Icon className={`w-5 h-5 ${product.color}`} />
                </div>
                <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-slate-900 dark:text-white">{product.title}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400 truncate">{product.description}</p>
                </div>
                <a
                    href={product.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap"
                >
                    {product.cta} <ArrowUpRight className="w-3 h-3" />
                </a>
            </m.div>
        </AnimatePresence>
    );
}
