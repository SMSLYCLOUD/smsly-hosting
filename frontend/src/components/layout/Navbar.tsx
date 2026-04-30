'use client';

import * as React from 'react';
import Link from 'next/link';
import { featureFlags } from '@/lib/featureFlags';
import { usePathname, useRouter } from 'next/navigation';
import { Settings, Menu, X, Home, LogOut, Rocket, CreditCard, Sparkles, Monitor, Radio, Brain, Archive, Shield, Layout, FolderKanban, Activity, Zap, Gauge, Network, FileCode, ArrowLeftRight, GitCompare } from 'lucide-react';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/ui/mode-toggle';
import { motion, AnimatePresence } from 'framer-motion';
import { clearAuthCookies } from '@/lib/auth-cookies';

export function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = React.useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = React.useState(false);
  const [isScrolled, setIsScrolled] = React.useState(false);
  const [user, setUser] = React.useState<{email?: string; is_staff?: boolean} | null>(null);
  const pathname = usePathname();
  const router = useRouter();
  const userMenuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 10);
    };
    window.addEventListener('scroll', handleScroll);

    // Check auth
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
    if (token) {
        // Fetch user data and resolve admin capability via admin-only endpoint.
        (async () => {
          try {
            const userRes = await fetch(`${window.location.origin}/api/v1/auth/user/`, {
              headers: { 'Authorization': `Token ${token}` },
            });
            if (!userRes.ok) {
              throw new Error('unauthorized');
            }
            const data = await userRes.json();

            let isStaff = Boolean(data?.is_staff || data?.is_superuser);
            if (!isStaff) {
              const adminRes = await fetch(`${window.location.origin}/api/v1/system/config/`, {
                headers: { 'Authorization': `Token ${token}` },
              });
              isStaff = adminRes.ok;
            }

            setUser({
              email: data?.email || data?.username || 'User',
              is_staff: isStaff,
            });
          } catch {
            localStorage.removeItem('auth_token');
            clearAuthCookies();
            setUser(null);
          }
        })();
    }

    const handleClickOutside = (event: MouseEvent) => {
        if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
            setIsUserMenuOpen(false);
        }
    };
    document.addEventListener('mousedown', handleClickOutside);

    return () => {
        window.removeEventListener('scroll', handleScroll);
        document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('auth_token');
    clearAuthCookies();
    setUser(null);
    setIsUserMenuOpen(false);
    router.push('/login');
  };

  const publicLinks = [
    { href: '/#features', label: 'Features' },
    { href: '/store', label: 'Templates' },
    { href: '/marketplace', label: 'Addons' },
    { href: '/pricing', label: 'Pricing' },
    { href: '/docs', label: 'Docs' },
    { href: '/status', label: 'Status' },
  ];

  const authLinks: Array<{
    href: string;
    label: string;
    icon: any;
    tier: 'primary' | 'secondary' | 'tertiary';
  }> = [
    { href: '/client', label: 'Client Area', icon: Home, tier: 'primary' },
    { href: '/dashboard', label: 'Dashboard', icon: Home, tier: 'primary' },
    { href: '/projects', label: 'Projects', icon: FolderKanban, tier: 'primary' },
    { href: '/services', label: 'Services', icon: Layout, tier: 'primary' },
    { href: '/deployments', label: 'Deployments', icon: Rocket, tier: 'primary' },
    { href: '/ecosystem', label: 'Ecosystem', icon: Sparkles, tier: 'primary' },
    { href: '/intelligence', label: 'Intelligence', icon: Brain, tier: 'primary' },
    { href: '/servers', label: 'Servers', icon: Monitor, tier: 'secondary' },
    { href: '/autoscaler', label: 'Autoscaler', icon: Gauge, tier: 'secondary' },
    { href: '/topology', label: 'Topology', icon: Network, tier: 'secondary' },
    { href: '/replication', label: 'Replication', icon: GitCompare, tier: 'secondary' },
    { href: '/tunnels', label: 'Tunnels', icon: Radio, tier: 'secondary' },
    { href: '/network', label: 'VPN Mesh', icon: Shield, tier: 'secondary' },
    { href: '/activity', label: 'Activity', icon: Activity, tier: 'tertiary' },
    { href: '/functions', label: 'Functions', icon: Zap, tier: 'tertiary' },
    { href: '/templates', label: 'Templates', icon: FileCode, tier: 'tertiary' },
    { href: '/transfers', label: 'Transfers', icon: ArrowLeftRight, tier: 'tertiary' },
    { href: '/billing', label: 'Billing', icon: CreditCard, tier: 'tertiary' },
    { href: '/settings', label: 'Settings', icon: Settings, tier: 'tertiary' },
  ];

  if (user?.is_staff) {
    authLinks.push({ href: '/backups', label: 'Backups', icon: Archive, tier: 'tertiary' });
    authLinks.push({ href: '/admin-dashboard/users', label: 'Admin', icon: Shield, tier: 'tertiary' });
  }

  const hiddenByFlag = new Set<string>([
    ...(featureFlags.autoscaler ? [] : ['/autoscaler']),
    ...(featureFlags.replication ? [] : ['/replication']),
    ...(featureFlags.tunnels ? [] : ['/tunnels']),
    ...(featureFlags.vpnMesh ? [] : ['/network']),
    ...(featureFlags.functions ? [] : ['/functions']),
    ...(featureFlags.transfers ? [] : ['/transfers']),
  ]);

  const visibleAuthLinks = authLinks.filter((link) => !hiddenByFlag.has(link.href));

  const primaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'primary');
  const secondaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'secondary');
  const tertiaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'tertiary');

  return (
    <nav
        className={`sticky top-0 z-50 w-full transition-all duration-300 backdrop-blur-md ${
            isScrolled
            ? 'bg-white/30 dark:bg-slate-950/30 shadow-sm border-b border-white/20'
            : 'bg-transparent border-b border-transparent'
        }`}
    >
      <div className="w-full grid h-14 grid-cols-[auto_1fr_auto] items-center gap-3 px-4 sm:px-6 lg:px-8">

        {/* Logo - Left */}
        <Link href={user ? '/client' : '/'} prefetch={false} className="flex items-center group flex-shrink-0 gap-2.5">
            <Image src="/images/logo.svg" alt="CloudNeuron Logo" width={28} height={28} className="h-7 w-7 shadow-sm rounded-lg" priority />
            {!user && <span className="font-bold text-lg tracking-tight text-slate-900 dark:text-white hidden sm:block">CloudNeuron</span>}
        </Link>

        {/* Nav Links - Center: Show public when logged out, auth when logged in */}
        <nav className="hidden min-w-0 items-center justify-center md:flex md:flex-1">
            <div
              className={
                user
                  ? "grid w-full grid-flow-col auto-cols-fr gap-1 rounded-xl border border-border/60 bg-background/40 p-1 shadow-sm backdrop-blur-md"
                  : "flex max-w-full items-center justify-center gap-0.5 overflow-x-auto whitespace-nowrap [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
              }
            >
            {!user && publicLinks.map((link) => {
                const isActive = pathname === link.href;
                return (
                     <Link
                         key={link.href}
                         href={link.href}
                         prefetch={false}
                         className={`
                             shrink-0 px-3 py-1.5 rounded-md text-[13px] font-medium transition-colors
                             ${isActive
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-slate-700 dark:text-slate-300 hover:text-emerald-600 dark:hover:text-emerald-400'}
                        `}
                    >
                        {link.label}
                    </Link>
                );
            })}
            {user && primaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                     <Link
                         key={link.href}
                         href={link.href}
                         prefetch={false}
                         title={link.label}
                         className={`
                             relative min-w-0 w-full px-2 py-1.5 rounded-lg text-[12px] lg:text-[12.5px] font-[600] tracking-[0.015em] transition-all duration-200 flex items-center justify-center gap-1.5
                             ${isActive
                                ? 'text-foreground bg-primary/10 shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.25)]'
                                : 'text-muted-foreground hover:text-foreground hover:bg-muted/70'}
                        `}
                    >
                        <Icon size={14} className="hidden xl:block shrink-0" />
                        <span className="truncate">{link.label}</span>
                        {isActive && (
                            <motion.div
                                layoutId="navbar-indicator"
                                className="absolute bottom-0 left-2 right-2 h-0.5 bg-primary rounded-full"
                                initial={false}
                                transition={{ type: "spring", stiffness: 500, damping: 30 }}
                            />
                        )}
                    </Link>
                );
            })}
            </div>
        </nav>

        {/* Right Side Buttons (Desktop) */}
        <div className="hidden items-center justify-end space-x-3 md:flex">
          {user && (
           <Link href="/new" prefetch={false}>
               <Button size="sm" className="bg-gradient-to-r from-emerald-500 to-green-600 hover:from-emerald-600 hover:to-green-700 text-white font-bold shadow-lg shadow-emerald-500/20 transition-all hover:scale-105 hover:shadow-emerald-500/30">
                   Deploy
               </Button>
           </Link>
          )}

          <div className="w-px h-6 bg-border mx-2" />

          <ModeToggle />

          {user ? (
             <div className="relative" ref={userMenuRef}>
                <Button
                    variant="ghost"
                    className="relative h-9 w-9 rounded-full overflow-hidden border border-border"
                    onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
                    aria-label="User menu"
                    aria-haspopup="true"
                    aria-expanded={isUserMenuOpen}
                    aria-controls="user-menu"
                >
                    <div className="w-full h-full bg-muted flex items-center justify-center text-muted-foreground font-semibold">
                        {user.email?.[0].toUpperCase()}
                    </div>
                </Button>

                <AnimatePresence>
                {isUserMenuOpen && (
                    <motion.div
                        id="user-menu"
                        initial={{ opacity: 0, y: 10, scale: 0.95 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 10, scale: 0.95 }}
                        transition={{ duration: 0.1 }}
                        className="absolute right-0 mt-2 w-56 bg-card border border-border rounded-md shadow-lg py-1 z-50 text-card-foreground"
                    >
                        <div className="px-3 py-2 border-b border-border">
                            <p className="text-sm font-medium">My Account</p>
                            <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                        </div>
                        <button
                            className="w-full text-left px-3 py-2 text-sm hover:bg-muted flex items-center gap-2"
                            onClick={() => router.push('/settings')}
                        >
                            <Settings size={14} /> Settings
                        </button>
                        <button
                            className="w-full text-left px-3 py-2 text-sm hover:bg-red-500/10 text-red-500 flex items-center gap-2"
                            onClick={handleLogout}
                        >
                            <LogOut size={14} /> Log out
                        </button>
                    </motion.div>
                )}
                </AnimatePresence>
             </div>
          ) : (
            <Link href="/login" prefetch={false}>
                <Button variant="ghost" size="sm">Login</Button>
            </Link>
          )}
        </div>

        {/* Mobile Menu Button */}
        <div className="flex md:hidden items-center gap-4">
            <ModeToggle />
            <button
                className="text-foreground focus:outline-none p-2 rounded-md hover:bg-muted"
                onClick={() => setIsMenuOpen(!isMenuOpen)}
                aria-label={isMenuOpen ? "Close menu" : "Open menu"}
                aria-expanded={isMenuOpen}
                aria-controls="mobile-menu"
            >
                {isMenuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
        </div>
      </div>

      {/* Row 2+3: Infrastructure & Tools — single container, divided */}
      {user && (secondaryAuthLinks.length > 0 || tertiaryAuthLinks.length > 0) && (
        <div className="hidden md:block border-t border-border/50 bg-card/60 backdrop-blur-sm">
          <div className="w-full px-4 sm:px-6 lg:px-8 py-0.5">
            {/* Infrastructure row */}
            <div className="flex items-center justify-center gap-0.5 py-0.5">
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground/40 font-semibold mr-2 shrink-0">Infra</span>
              {secondaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    className={`shrink-0 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors flex items-center gap-1.5 ${
                      isActive
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                    }`}
                  >
                    <Icon size={12} />
                    {link.label}
                  </Link>
                );
              })}
            </div>
            {/* Subtle divider */}
            <div className="border-t border-border/30 mx-8" />
            {/* Tools row */}
            <div className="flex items-center justify-center gap-0.5 py-0.5">
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground/40 font-semibold mr-2 shrink-0">Tools</span>
              {tertiaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    className={`shrink-0 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors flex items-center gap-1.5 ${
                      isActive
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                    }`}
                  >
                    <Icon size={12} />
                    {link.label}
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Mobile Menu Dropdown */}
      <AnimatePresence>
        {isMenuOpen && (
            <motion.div
                id="mobile-menu"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="md:hidden bg-background border-b border-border overflow-hidden"
            >
                <div className="p-4 space-y-4">
                    {user && (
                    <nav className="flex flex-col space-y-2">
                        {authLinks.map((link) => (
                             <Link
                                 key={link.href}
                                 href={link.href}
                                 prefetch={false}
                                 className={`flex items-center gap-3 p-3 rounded-lg font-medium ${
                                     pathname === link.href ? 'bg-primary/10 text-primary' : 'text-foreground/80 hover:bg-muted'
                                 }`}
                                onClick={() => setIsMenuOpen(false)}
                            >
                                <link.icon size={20} /> {link.label}
                            </Link>
                        ))}
                    </nav>
                    )}
                    <div className="pt-4 border-t border-border flex flex-col gap-3">
                        {user && (
                        <Link href="/new" prefetch={false} onClick={() => setIsMenuOpen(false)}>
                            <Button className="w-full bg-gradient-to-r from-emerald-500 to-green-600 text-white font-bold h-11">
                                Deploy Project
                            </Button>
                        </Link>
                        )}
                        {user ? (
                             <Button variant="destructive" className="w-full justify-start" onClick={() => { handleLogout(); setIsMenuOpen(false); }}>
                                <LogOut className="mr-2 h-4 w-4" /> Log out
                             </Button>
                        ) : (
                            <Link href="/login" prefetch={false} onClick={() => setIsMenuOpen(false)}>
                                <Button variant="outline" className="w-full">Login</Button>
                            </Link>
                        )}
                    </div>
                </div>
            </motion.div>
        )}
      </AnimatePresence>
    </nav>
  );
}