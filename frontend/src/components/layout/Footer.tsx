import Link from 'next/link';
import { Cloud, Globe, GitBranch } from 'lucide-react';

export function Footer() {
    return (
        <footer className="py-12 md:py-16 px-6 border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950">
            <div className="max-w-7xl mx-auto">
                <div className="grid md:grid-cols-4 gap-8 md:gap-12 mb-8 md:mb-12">
                    <div className="col-span-1 md:col-span-1">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-10 h-10 bg-emerald-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-emerald-500/20">
                                <Cloud className="w-6 h-6" />
                            </div>
                            <span className="font-bold text-xl text-slate-900 dark:text-white tracking-tight">CloudNeuron</span>
                        </div>
                        <p className="text-slate-500 dark:text-slate-400 text-sm leading-relaxed mb-6">
                            The intelligent cloud platform for modern engineering teams. Deploy, scale, and manage with ease.
                        </p>
                        <div className="flex gap-4">
                            {/* Social Icons Placeholder */}
                            <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-500 hover:bg-emerald-100 hover:text-emerald-600 transition-colors cursor-pointer">
                                <Globe className="w-4 h-4" />
                            </div>
                            <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-500 hover:bg-emerald-100 hover:text-emerald-600 transition-colors cursor-pointer">
                                <GitBranch className="w-4 h-4" />
                            </div>
                        </div>
                    </div>

                    <div>
                        <h4 className="font-bold text-slate-900 dark:text-white mb-4 md:mb-6">Product</h4>
                        <ul className="space-y-3 text-sm text-slate-500 dark:text-slate-400">
                            <li><Link href="/#features" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Features</Link></li>
                            <li><Link href="/pricing" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Pricing</Link></li>
                            <li><Link href="/compare" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Compare vs Managed PaaS</Link></li>
                            <li><Link href="/contact" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Enterprise</Link></li>
                            <li><Link href="/docs" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Changelog</Link></li>
                        </ul>
                    </div>

                    <div>
                        <h4 className="font-bold text-slate-900 dark:text-white mb-4 md:mb-6">Resources</h4>
                        <ul className="space-y-3 text-sm text-slate-500 dark:text-slate-400">
                            <li><Link href="/docs" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Documentation</Link></li>
                            <li><Link href="/docs/install" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Guides</Link></li>
                            <li><Link href="/docs" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">API Reference</Link></li>
                            <li><Link href="/status" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">System Status</Link></li>
                        </ul>
                    </div>

                    <div>
                        <h4 className="font-bold text-slate-900 dark:text-white mb-4 md:mb-6">Legal</h4>
                        <ul className="space-y-3 text-sm text-slate-500 dark:text-slate-400">
                            <li><Link href="/docs" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Privacy Policy</Link></li>
                            <li><Link href="/docs" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Terms of Service</Link></li>
                            <li><Link href="/docs/install#security-hardening" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Security</Link></li>
                        </ul>
                    </div>
                </div>

                <div className="pt-8 border-t border-slate-200 dark:border-slate-800 flex flex-col md:flex-row items-center justify-between gap-4">
                    <div className="text-slate-500 dark:text-slate-400 text-sm">
                        © 2026 CloudNeuron Inc. All rights reserved.
                    </div>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                        All Systems Operational
                    </div>
                </div>
            </div>
        </footer>
    );
}
