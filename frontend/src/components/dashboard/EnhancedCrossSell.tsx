'use client';

import { useState, useEffect } from 'react';
import { MessageSquare, Fingerprint, TrendingUp, ArrowUpRight } from 'lucide-react';
import { motion } from 'framer-motion';

const products = [
    {
        id: 'sms-apis',
        title: 'SMSLY Communication APIs',
        description: 'SMS, WhatsApp, Voice, Email — global messaging for your apps.',
        icon: MessageSquare,
        color: 'text-blue-500',
        bg: 'bg-blue-500/10',
        href: 'https://smsly.cloud',
        cta: 'Get API keys'
    },
    {
        id: 'identity',
        title: 'SMSLY Identity & Trust',
        description: 'SilentOTP, verification, abuse prevention, and media integrity.',
        icon: Fingerprint,
        color: 'text-emerald-500',
        bg: 'bg-emerald-500/10',
        href: 'https://smsly.cloud',
        cta: 'Secure your app'
    },
    {
        id: 'ignite',
        title: 'Ignite Growth Automation',
        description: 'AI-assisted marketing planning, publishing, lead gen, and analytics.',
        icon: TrendingUp,
        color: 'text-amber-500',
        bg: 'bg-amber-500/10',
        href: 'https://smsly.cloud',
        cta: 'Grow faster'
    }
];

const ROTATE_INTERVAL = 8000;

export function EnhancedCrossSell({ variant = 'card', dismissible = true }: { 
    variant?: 'card' | 'banner' | 'compact';
    dismissible?: boolean;
}) {
    const [currentIndex, setCurrentIndex] = useState(0);
    const [dismissed, setDismissed] = useState(false);

    useEffect(() => {
        if (!dismissed) {
            const interval = setInterval(() => {
                setCurrentIndex(prev => (prev + 1) % products.length);
            }, ROTATE_INTERVAL);
            return () => clearInterval(interval);
        }
    }, [dismissed]);

    if (dismissed) return null;

    const product = products[currentIndex];
    const Icon = product.icon;

     if (variant === 'banner') {
         return (
             <motion.div
                 key={product.id}
                 initial={{ opacity: 0, y: -10 }}
                 animate={{ opacity: 1, y: 0 }}
                 exit={{ opacity: 0, y: -10 }}
                 className="relative rounded-xl border border-slate-200 dark:border-slate-700 bg-gradient-to-r from-slate-50 to-white dark:from-slate-900 dark:to-slate-950 p-8 flex items-center gap-6 mb-8"
             >
                 <div className="flex-shrink-0">
                     <div className={`p-4 rounded-lg ${product.bg}`}>
                         <Icon className={`w-8 h-8 ${product.color}`} />
                     </div>
                 </div>
                 <div className="flex-1 min-w-0">
                     <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-2">{product.title}</h2>
                     <p className="text-base text-slate-500 dark:text-slate-400 mb-4">{product.description}</p>
                 </div>
                 <div className="flex-shrink-0 space-x-4">
                     <a
                         href={product.href}
                         target="_blank"
                         rel="noopener noreferrer"
                         className="flex items-center gap-2 text-sm font-semibold text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap"
                     >
                         {product.cta} <ArrowUpRight className="w-4 h-4" />
                     </a>
                     {dismissible && (
                         <button
                             onClick={() => setDismissed(true)}
                             className="p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors"
                             aria-label="Dismiss"
                         >
                             <span className="sr-only">Dismiss</span>
                             <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-slate-400 hover:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                             </svg>
                         </button>
                     )}
                 </div>
             </motion.div>
         );
     }

    if (variant === 'compact') {
        return (
            <motion.div
                key={product.id}
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 4 }}
                className="relative rounded-lg border border-slate-200 dark:border-slate-700 bg-gradient-to-r from-slate-50 to-white dark:from-slate-900 dark:to-slate-950 p-3 flex items-center gap-3 mb-4"
            >
                <div className={`p-2 rounded-lg ${product.bg} flex-shrink-0`}>
                    <Icon className={`w-4 h-4 ${product.color}`} />
                </div>
                <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-slate-900 dark:text-white">{product.title}</p>
                    <p className="text-[10px] text-slate-400 dark:text-slate-300 truncate">{product.description}</p>
                </div>
                <div className="flex-shrink-0 space-x-2">
                    <a
                        href={product.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-0.5 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap"
                    >
                        {product.cta} <ArrowUpRight className="w-2.5 h-2.5" />
                    </a>
                    {dismissible && (
                        <button
                            onClick={() => setDismissed(true)}
                            className="p-0.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded transition-colors"
                            aria-label="Dismiss"
                        >
                            <span className="sr-only">Dismiss</span>
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3 text-slate-400 hover:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        </button>
                    )}
                </div>
            </motion.div>
        );
    }

    // Default card variant
    return (
        <motion.div
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
        </motion.div>
    );
}