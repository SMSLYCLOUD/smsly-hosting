'use client';

import * as React from 'react';
import Link from 'next/link';
import { featureFlags } from '@/lib/featureFlags';
import { shouldShowAllNav } from '@/lib/nav-visibility';
import { usePathname, useRouter } from 'next/navigation';
import { Settings, Menu, X, Home, LogOut, Rocket, CreditCard, Sparkles, Monitor, Radio, Brain, Archive, Shield, Layout, FolderKanban, Activity, Zap, Gauge, Network, FileCode, ArrowLeftRight, GitCompare, Plug, Search } from 'lucide-react';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/ui/mode-toggle';
import { NotificationsDropdown } from './NotificationsDropdown';
import { m, AnimatePresence } from 'framer-motion';
import { api } from '@/lib/api';
import { logout as performLogout } from '@/lib/auth';

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

    // Fetch user via the cookie-authenticated axios instance. A 401
    // means the user is logged out and we keep the menu in the
    // anonymous state. There is no client-side token to read.
    (async () => {
      try {
        const userRes = await api.get('/auth/user/');
        if (!userRes.data) return;
        const data = userRes.data;

        let isStaff = Boolean(data?.is_staff || data?.is_superuser);
        if (!isStaff) {
          try {
            await api.get('/system/config/');
            isStaff = true;
          } catch {
            isStaff = false;
          }
        }

        setUser({
          email: data?.email || data?.username || 'User',
          is_staff: isStaff,
        });
      } catch {
        setUser(null);
      }
    })();

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
    setUser(null);
    setIsUserMenuOpen(false);
    performLogout();
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
    { href: '/mcp', label: 'MCP Server', icon: Plug, tier: 'tertiary' },
    { href: '/settings', label: 'Settings', icon: Settings, tier: 'tertiary' },
    { href: '/status', label: 'System Status', icon: Activity, tier: 'tertiary' },
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

  const showAll = shouldShowAllNav();
  const visibleAuthLinks = authLinks.filter((link) => showAll || !hiddenByFlag.has(link.href));

  const primaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'primary');
  const secondaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'secondary');
  const tertiaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'tertiary');

  return (
    <nav
        className={`sticky top-0 z-50 w-full transition-all duration-300 border-b border-white/5 bg-[#0a0c10] shadow-2xl`}
    >
      <div className="w-full grid h-14 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 px-4 sm:px-6 lg:px-8 max-w-[2200px] mx-auto">

        {/* Logo - Left */}
        <Link href={user ? '/client' : '/'} prefetch={false} className="flex items-center group flex-shrink-0 gap-2.5">
            <div className="rounded-md bg-white p-0.5 shadow-lg shadow-emerald-500/10 group-hover:scale-105 transition-transform">
                <Image src="/images/mini_logo.png" alt="Grid" width={24} height={28} className="h-7 w-auto object-contain" priority />
            </div>
            <div className="flex flex-col">
              <span className="font-bold text-lg tracking-tight text-white leading-none">Grid</span>
              {!user && (
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[8px] font-bold text-emerald-500 tracking-[0.15em] uppercase">Secured By</span>
                  <span className="text-[9px] font-extrabold text-white/70 tracking-[0.05em] uppercase">TruLay</span>
                </div>
              )}
            </div>
        </Link>

        {/* Nav Links - Center */}
        <nav className="hidden min-w-0 items-center justify-center md:flex md:flex-1">
            <div
              className={
                user
                  ? "grid w-full max-w-[1180px] grid-cols-7 items-center gap-1 rounded-xl border border-white/5 bg-[#12151c]/60 p-1"
                  : "flex items-center gap-1 overflow-x-auto"
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
                             shrink-0 px-4 py-1.5 rounded-lg text-[13px] font-medium transition-colors
                             ${isActive
                                ? 'text-emerald-400 bg-emerald-500/10'
                                : 'text-zinc-400 hover:text-white hover:bg-white/5'}
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
                             relative flex h-9 min-w-0 items-center justify-center gap-2 rounded-lg px-2 text-[12px] font-semibold tracking-normal transition-all duration-200
                             ${isActive
                                ? 'text-white bg-[#1e232d] shadow-[0_0_0_1px_rgba(255,255,255,0.05)] border border-white/10'
                                : 'text-zinc-500 hover:text-zinc-200 hover:bg-white/5'}
                        `}
                    >
                        <Icon size={15} className={`shrink-0 ${isActive ? 'text-emerald-400' : 'text-zinc-600'}`} />
                        <span className="min-w-0 truncate">{link.label}</span>
                    </Link>
                );
            })}
            </div>
        </nav>

        {/* Right Side Buttons (Desktop) */}
        <div className="hidden items-center justify-end space-x-2 md:flex">
          {user && (
           <Link href="/new" prefetch={false}>
               <Button size="sm" className="bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold px-3 rounded-lg shadow-lg shadow-emerald-500/20 transition-all hover:scale-105 active:scale-95">
                   Deploy
               </Button>
           </Link>
          )}

          <div className="w-px h-6 bg-border mx-2" />

          <button
            onClick={() => {
              document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
            }}
            className="flex items-center gap-2 h-9 rounded-lg border border-border bg-white/5 px-3 text-sm text-muted-foreground hover:bg-white/10 transition-colors"
          >
            <Search className="h-4 w-4" />
            <span className="hidden lg:inline">Search</span>
            <kbd className="pointer-events-none hidden select-none rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground lg:inline">
              <span className="text-xs">&#8984;</span>K
            </kbd>
          </button>

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
                    <m.div
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
                    </m.div>
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

      {/* Row 2+3: Infrastructure & Tools ΓÇö single container, divided */}
      {user && (secondaryAuthLinks.length > 0 || tertiaryAuthLinks.length > 0) && (
        <div className="hidden md:block border-t border-white/5 bg-[#0a0c10]/80 backdrop-blur-md">
          <div className="w-full px-4 sm:px-6 lg:px-8 py-1 max-w-[2200px] mx-auto">
            {/* Infrastructure row */}
            <div className="flex items-center justify-center gap-1 py-1">
              <span className="text-[9px] uppercase tracking-[0.2em] text-zinc-600 font-bold mr-4 shrink-0">Infra</span>
              {secondaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    className={`shrink-0 px-3 py-1 rounded-md text-[11px] font-semibold transition-colors flex items-center gap-2 ${
                      isActive
                        ? 'bg-emerald-500/10 text-emerald-400'
                        : 'text-zinc-500 hover:text-zinc-200 hover:bg-white/5'
                    }`}
                  >
                    <Icon size={12} className={isActive ? 'text-emerald-500' : 'text-zinc-600'} />
                    {link.label}
                  </Link>
                );
              })}
            </div>
            {/* Subtle divider */}
            <div className="border-t border-white/5 mx-auto max-w-2xl" />
            {/* Tools row */}
            <div className="flex items-center justify-center gap-1 py-1">
              <span className="text-[9px] uppercase tracking-[0.2em] text-zinc-600 font-bold mr-4 shrink-0">Tools</span>
              {tertiaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    className={`shrink-0 px-3 py-1 rounded-md text-[11px] font-semibold transition-colors flex items-center gap-2 ${
                      isActive
                        ? 'bg-emerald-500/10 text-emerald-400'
                        : 'text-zinc-500 hover:text-zinc-200 hover:bg-white/5'
                    }`}
                  >
                    <Icon size={12} className={isActive ? 'text-emerald-500' : 'text-zinc-600'} />
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
            <m.div
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
            </m.div>
        )}
      </AnimatePresence>
    </nav>
  );
}
