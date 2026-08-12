'use client';

import { useState } from 'react';
import { Lightbulb, Sparkles, X, ArrowUpRight } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

type SuggestionContext = 'dashboard' | 'services' | 'networking' | 'addons' | 'intelligence' | 'marketplace' | 'billing' | 'settings' | 'general';

interface Suggestion {
    title: string;
    description: string;
    icon: any;
    href: string;
    cta: string;
    color: string;
    bg: string;
}

const suggestionsMap: Record<SuggestionContext, Suggestion> = {
    dashboard: {
        title: "Pro Tip: Automate your marketing with Ignite",
        description: "Did you know? You can auto-generate and schedule social posts directly from your Grid services using the Ignite Growth API.",
        icon: Sparkles,
        color: "text-amber-600 dark:text-amber-400",
        bg: "bg-amber-500/10 border-amber-500/20",
        href: "https://Trulay.co",
        cta: "Explore Ignite"
    },
    services: {
        title: "Ecosystem Insight",
        description: "Scale smarter. Connect your backend services securely to Trulay Communication APIs without leaving the private network.",
        icon: Lightbulb,
        color: "text-blue-600 dark:text-blue-400",
        bg: "bg-blue-500/10 border-blue-500/20",
        href: "https://Trulay.co",
        cta: "View Communication APIs"
    },
    networking: {
        title: "Security Recommendation",
        description: "Enforce zero-trust access. Use Trulay Identity to wrap your exposed tunnels and dev domains with SilentOTP and Abuse Prevention.",
        icon: Lightbulb,
        color: "text-emerald-600 dark:text-emerald-400",
        bg: "bg-emerald-500/10 border-emerald-500/20",
        href: "https://Trulay.co",
        cta: "Enable Identity"
    },
    addons: {
        title: "Pro Tip: Seamless Integration",
        description: "Your addons run inside the isolated Grid mesh. Connect them securely to your Trulay Identity layers for compliant, highly-available data isolation.",
        icon: Lightbulb,
        color: "text-purple-600 dark:text-purple-400",
        bg: "bg-purple-500/10 border-purple-500/20",
        href: "https://Trulay.co",
        cta: "Learn more"
    },
    intelligence: {
        title: "Accelerate your AI",
        description: "Running Ollama or DeepSeek? Pipe your inference data through the Ignite Analytics engine to monitor usage and user sentiment automatically.",
        icon: Sparkles,
        color: "text-violet-600 dark:text-violet-400",
        bg: "bg-violet-500/10 border-violet-500/20",
        href: "https://Trulay.co",
        cta: "View Analytics integration"
    },
    marketplace: {
        title: "Explore the Ecosystem",
        description: "Grid natively supports 35+ addons, but you can also integrate external services like Trulay managed email and voice APIs in one click.",
        icon: Lightbulb,
        color: "text-indigo-600 dark:text-indigo-400",
        bg: "bg-indigo-500/10 border-indigo-500/20",
        href: "https://Trulay.co",
        cta: "See all integrations"
    },
    billing: {
        title: "Cost Optimization Tip",
        description: "You're saving up to 90% running on Grid versus managed clouds. Keep scaling without worrying about per-seat vendor lock-in.",
        icon: Lightbulb,
        color: "text-emerald-600 dark:text-emerald-400",
        bg: "bg-emerald-500/10 border-emerald-500/20",
        href: "https://Trulay.co",
        cta: "View architecture guide"
    },
    settings: {
        title: "Pro Tip: Platform Audits",
        description: "Enable Trulay Identity audit logs to track every user action, token issuance, and setting change across your ecosystem.",
        icon: Lightbulb,
        color: "text-blue-600 dark:text-blue-400",
        bg: "bg-blue-500/10 border-blue-500/20",
        href: "https://Trulay.co",
        cta: "Configure audits"
    },
    general: {
        title: "Platform Tip",
        description: "Grid connects seamlessly with the full Trulay ecosystem — the trust layer for internet communications and identity.",
        icon: Lightbulb,
        color: "text-slate-600 dark:text-slate-400",
        bg: "bg-slate-500/10 border-slate-500/20",
        href: "https://Trulay.co",
        cta: "Learn more"
    }
};

export function EcosystemSuggestion({ context = 'general', dismissible = true, className = '' }: { 
    context?: SuggestionContext;
    dismissible?: boolean;
    className?: string;
}) {
    const [dismissed, setDismissed] = useState(false);

    if (dismissed) return null;

    const suggestion = suggestionsMap[context] || suggestionsMap.general;
    const Icon = suggestion.icon;

    return (
        <AnimatePresence>
            {!dismissed && (
                <motion.div
                    initial={{ opacity: 0, y: -4, height: 0 }}
                    animate={{ opacity: 1, y: 0, height: 'auto' }}
                    exit={{ opacity: 0, y: -4, height: 0 }}
                    className={`relative rounded-xl border ${suggestion.bg} bg-white/50 dark:bg-slate-900/50 backdrop-blur-sm p-4 flex flex-col sm:flex-row items-start sm:items-center gap-4 my-6 shadow-sm ${className}`}
                >
                    <div className={`p-2.5 rounded-xl bg-white dark:bg-slate-800 shadow-sm border border-slate-100 dark:border-slate-700 flex-shrink-0`}>
                        <Icon className={`w-5 h-5 ${suggestion.color}`} />
                    </div>
                    
                    <div className="flex-1 min-w-0">
                        <h4 className={`text-sm font-bold ${suggestion.color} mb-1`}>{suggestion.title}</h4>
                        <p className="text-sm text-slate-600 dark:text-slate-400">
                            {suggestion.description}
                        </p>
                    </div>
                    
                    <div className="flex-shrink-0 flex items-center gap-3 self-start sm:self-center mt-3 sm:mt-0">
                        <a
                            href={suggestion.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={`flex items-center gap-1.5 text-xs font-semibold ${suggestion.color} hover:underline whitespace-nowrap bg-white dark:bg-slate-800 px-4 py-2 rounded-lg shadow-sm border border-slate-200 dark:border-slate-700 transition-colors`}
                        >
                            {suggestion.cta} <ArrowUpRight className="w-3.5 h-3.5" />
                        </a>
                        
                        {dismissible && (
                            <button
                                onClick={() => setDismissed(true)}
                                className="p-2 hover:bg-black/5 dark:hover:bg-white/10 rounded-lg transition-colors text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                                aria-label="Dismiss suggestion"
                            >
                                <X size={16} />
                            </button>
                        )}
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}