'use client';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { Globe, GitBranch } from 'lucide-react';

export function Footer() {
    const pathname = usePathname();
    
    // List of authenticated/app routes where the footer should be hidden
    const appRoutes = [
        '/client', '/dashboard', '/projects', '/services', '/deployments',
        '/ecosystem', '/intelligence', '/servers', '/autoscaler', '/topology',
        '/replication', '/tunnels', '/network', '/activity', '/functions',
        '/templates', '/transfers', '/settings', '/backups', '/admin-dashboard',
        '/new'
    ];

    const isAppRoute = appRoutes.some(route => pathname?.startsWith(route));

    if (isAppRoute) {
        return null;
    }

    return (
        <footer className="py-12 md:py-16 px-6 border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950">
            <div className="max-w-7xl mx-auto">
                <div className="grid md:grid-cols-4 gap-8 md:gap-12 mb-8 md:mb-12">
                    <div className="col-span-1 md:col-span-1">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="rounded-md bg-white p-1 shadow-lg shadow-emerald-500/10">
                                <Image src="/images/mini_logo.png" alt="Grid" width={24} height={28} className="h-7 w-auto object-contain" />
                            </div>
                            <div className="flex flex-col">
                                <span className="font-bold text-xl text-slate-900 dark:text-white tracking-tight leading-none">Grid</span>
                                <div className="flex items-center gap-1 mt-1">
                                    <span className="text-[7px] font-bold text-emerald-500 tracking-[0.1em] uppercase">Secured By</span>
                                    <span className="text-[8px] font-extrabold text-slate-500 dark:text-slate-400 tracking-[0.05em] uppercase">SMSLYCLOUD</span>
                                </div>
                            </div>
                        </div>
                            Grid is a free, open-source PaaS. Deploy complete software ecosystems on infrastructure you control.
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
                            <li><Link href="/docs/changelog" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Changelog</Link></li>
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
                            <li><Link href="/legal/privacy" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Privacy Policy</Link></li>
                            <li><Link href="/legal/terms" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Terms of Service</Link></li>
                            <li><Link href="/docs/security" className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors">Security</Link></li>
                        </ul>
                    </div>
                </div>

                <div className="pt-8 border-t border-slate-200 dark:border-slate-800 flex flex-col md:flex-row items-center justify-between gap-4">
                    <div className="text-slate-500 dark:text-slate-400 text-sm flex items-center gap-2">
                        © 2026 Grid. All rights reserved.
                        <span className="text-slate-300 dark:text-slate-700 mx-2">|</span>
                        <span className="flex items-center gap-1.5 group">
                          Built with <span className="text-rose-500 animate-pulse">❤️</span> by 
                          <Link href="https://smsly.cloud" target="_blank" className="font-bold text-slate-700 dark:text-slate-300 hover:text-emerald-500 transition-colors">SMSLYCLOUD</Link>
                        </span>
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
