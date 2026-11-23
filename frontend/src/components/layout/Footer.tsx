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
        <footer className="py-12 md:py-16 px-6 border-t border-slate-200 dark:border-slate-800">
            <div className="max-w-7xl mx-auto">
                <div className="grid md:grid-cols-4 gap-8 md:gap-12 mb-8 md:mb-12">
                    <div className="col-span-1 md:col-span-1">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="rounded-md bg-white p-1 border border-slate-200 dark:border-transparent">
                                <Image src="/images/mini_logo.png" alt="Trulay Grid" width={24} height={28} className="h-7 w-auto object-contain" />
                            </div>
                            <div className="flex flex-col">
                                <span className="font-bold text-xl text-slate-900 dark:text-white tracking-tight leading-none">Grid</span>
                                <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 tracking-wide uppercase mt-1">
                                    Secured By Trulay
                                </span>
                            </div>
                        </div>
                        <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-5">
                            Grid is a free, open-source PaaS. Deploy complete software ecosystems on infrastructure you control.
                        </p>
                        <div className="flex gap-3">
                            <a
                                href="https://Trulay.co"
                                target="_blank"
                                rel="noopener noreferrer"
                                aria-label="Visit Trulay website"
                                className="w-11 h-11 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300 hover:bg-emerald-100 hover:text-emerald-700 dark:hover:bg-emerald-500/20 dark:hover:text-emerald-400 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                                <Globe className="w-4 h-4" aria-hidden="true" />
                            </a>
                            <a
                                href="https://github.com/SMSLYCLOUD/smsly-hosting"
                                target="_blank"
                                rel="noopener noreferrer"
                                aria-label="View Grid source on GitHub"
                                className="w-11 h-11 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300 hover:bg-emerald-100 hover:text-emerald-700 dark:hover:bg-emerald-500/20 dark:hover:text-emerald-400 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                                <GitBranch className="w-4 h-4" aria-hidden="true" />
                            </a>
                        </div>
                    </div>

                    <div>
                        <h2 className="text-sm font-bold text-slate-900 dark:text-white mb-4 md:mb-5">Product</h2>
                        <ul className="space-y-3 text-sm text-slate-600 dark:text-slate-400">
                            <li><Link href="/#features" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Features</Link></li>
                            <li><Link href="/pricing" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Pricing</Link></li>
                            <li><Link href="/compare" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Compare vs Managed PaaS</Link></li>
                            <li><Link href="/contact" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Enterprise</Link></li>
                            <li><Link href="/docs/changelog" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Changelog</Link></li>
                        </ul>
                    </div>

                    <div>
                        <h2 className="text-sm font-bold text-slate-900 dark:text-white mb-4 md:mb-5">Resources</h2>
                        <ul className="space-y-3 text-sm text-slate-600 dark:text-slate-400">
                            <li><Link href="/docs" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Documentation</Link></li>
                            <li><Link href="/docs/install" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Guides</Link></li>
                            <li><Link href="/docs/api" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">API Reference</Link></li>
                            <li><Link href="/status" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">System Status</Link></li>
                        </ul>
                    </div>

                    <div>
                        <h2 className="text-sm font-bold text-slate-900 dark:text-white mb-4 md:mb-5">Legal</h2>
                        <ul className="space-y-3 text-sm text-slate-600 dark:text-slate-400">
                            <li><Link href="/legal/privacy" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Privacy Policy</Link></li>
                            <li><Link href="/legal/terms" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Terms of Service</Link></li>
                            <li><Link href="/docs/security" className="inline-block py-1 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Security</Link></li>
                        </ul>
                    </div>
                </div>

                <div className="pt-8 border-t border-slate-200 dark:border-slate-800 flex flex-col md:flex-row items-center justify-between gap-4">
                    <div className="text-slate-600 dark:text-slate-400 text-sm flex flex-wrap items-center gap-2">
                        <span>© 2026 Grid. All rights reserved.</span>
                        <span className="text-slate-300 dark:text-slate-700 mx-1" aria-hidden="true">|</span>
                        <span className="flex items-center gap-1.5">
                          Built by{' '}
                          <a href="https://Trulay.co" target="_blank" rel="noopener noreferrer" className="font-bold text-slate-800 dark:text-slate-200 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors">Trulay</a>
                        </span>
                    </div>
                    <Link
                        href="/status"
                        className="inline-flex items-center gap-2 text-xs font-medium text-slate-600 dark:text-slate-300 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
                    >
                        <span className="w-2 h-2 rounded-full bg-emerald-500" aria-hidden="true" />
                        View Platform Status
                    </Link>
                </div>
            </div>
        </footer>
    );
}
