'use client';

import * as React from 'react';
import Link from 'next/link';
import { featureFlags } from '@/lib/featureFlags';
import { shouldShowAllNav } from '@/lib/nav-visibility';
import { usePathname, useRouter } from 'next/navigation';
import {
  Settings, Menu, X, Home, LogOut, Rocket, Sparkles, Monitor, Radio, Brain,
  Archive, Shield, Layout, FolderKanban, Activity, Zap, Gauge, Network,
  FileCode, ArrowLeftRight, GitCompare, Plug, Search, Crown,
} from 'lucide-react';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ModeToggle } from '@/components/ui/mode-toggle';
import { NotificationsDropdown } from './NotificationsDropdown';
import { m, AnimatePresence, useReducedMotion } from 'framer-motion';
import { api } from '@/lib/api';
import { logout as performLogout } from '@/lib/auth';
import { useAuth } from '@/components/auth-provider';

export function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = React.useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = React.useState(false);
  const [isStaffVerified, setIsStaffVerified] = React.useState<boolean | null>(null);
  const { user: authUser } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const headerRef = React.useRef<HTMLElement>(null);
  const prefersReducedMotion = useReducedMotion();

  React.useEffect(() => {
    let active = true;
    if (!authUser) {
      setIsStaffVerified(null);
      return;
    }
    const baseStaff = Boolean(authUser.is_staff || authUser.is_superuser);
    if (baseStaff) {
      setIsStaffVerified(true);
      return;
    }
    api.get('/system/config/')
      .then(() => {
        if (active) setIsStaffVerified(true);
      })
      .catch(() => {
        if (active) setIsStaffVerified(false);
      });
    return () => {
      active = false;
    };
  }, [authUser]);

  React.useEffect(() => {
    const el = headerRef.current;
    if (!el || typeof window === 'undefined') return;
    const syncHeight = () => {
      document.documentElement.style.setProperty('--navbar-height', `${el.offsetHeight}px`);
    };
    syncHeight();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(syncHeight);
    observer.observe(el);
    return () => observer.disconnect();
  }, [authUser, isMenuOpen]);

  React.useEffect(() => {
    setIsMenuOpen(false);
    setIsUserMenuOpen(false);
  }, [pathname]);

  const user = React.useMemo(() => {
    if (!authUser) return null;
    return {
      email: authUser.email || authUser.username || 'User',
      is_staff: isStaffVerified ?? Boolean(authUser.is_staff || authUser.is_superuser),
    };
  }, [authUser, isStaffVerified]);

  const handleLogout = () => {
    setIsUserMenuOpen(false);
    performLogout();
  };

  const openSearchPalette = () => {
    window.dispatchEvent(new CustomEvent('smsly:open-command-palette'));
  };

  const publicLinks = [
    { href: '/#features', label: 'Features' },
    { href: '/compare', label: 'Compare' },
    { href: '/store', label: 'Templates' },
    { href: '/docs/addons', label: 'Addons' },
    { href: '/pricing', label: 'Pricing' },
    { href: '/docs', label: 'Docs' },
    { href: '/status', label: 'Status' },
  ];

  const authLinks: Array<{
    href: string;
    label: string;
    icon: React.ComponentType<{ size?: number; className?: string }>;
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
    { href: '/clusters', label: 'Clusters', icon: Crown, tier: 'secondary' },
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
    ...(featureFlags.vpnMesh ? [] : ['/network', '/clusters']),
    ...(featureFlags.functions ? [] : ['/functions']),
    ...(featureFlags.transfers ? [] : ['/transfers']),
  ]);

  const showAll = shouldShowAllNav();
  const visibleAuthLinks = authLinks.filter((link) => showAll || !hiddenByFlag.has(link.href));

  const primaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'primary');
  const secondaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'secondary');
  const tertiaryAuthLinks = visibleAuthLinks.filter((link) => link.tier === 'tertiary');

  return (
    <header ref={headerRef} className="sticky top-0 z-50 w-full transition-colors duration-200 border-b border-white/10 bg-[#0a0c10] text-zinc-100">
      <div className="w-full grid h-14 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 lg:gap-4 px-4 sm:px-6 lg:px-8 max-w-[2200px] mx-auto">
        {/* Logo - Left */}
        <Link href={user ? '/client' : '/'} prefetch={false} className="flex items-center group flex-shrink-0 gap-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 rounded-md">
          <div className="rounded-md bg-white p-0.5 group-hover:scale-105 transition-transform">
            <Image src="/images/mini_logo.png" alt="Trulay Grid" width={24} height={28} className="h-7 w-auto object-contain" priority />
          </div>
          <div className="flex flex-col">
            <span className="font-bold text-lg tracking-tight text-white leading-none">Grid</span>
            {!user && (
              <div className="flex items-center gap-1 mt-0.5">
                <span className="text-[11px] font-semibold text-emerald-400 tracking-wide uppercase leading-none">By Trulay</span>
              </div>
            )}
          </div>
        </Link>

        {/* Nav Links - Center */}
        <nav aria-label="Primary navigation" className="hidden min-w-0 items-center justify-center md:flex md:flex-1">
          <div
            className={
              user
                ? 'grid w-full max-w-[1180px] grid-cols-7 items-center gap-1 rounded-xl border border-white/10 bg-[#12151c]/60 p-1'
                : 'flex items-center gap-1 overflow-x-auto'
            }
          >
            {!user &&
              publicLinks.map((link) => {
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    className={`shrink-0 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      isActive
                        ? 'text-emerald-400 bg-emerald-500/10'
                        : 'text-zinc-300 hover:text-white hover:bg-white/5'
                    }`}
                  >
                    {link.label}
                  </Link>
                );
              })}
            {user &&
              primaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    title={link.label}
                    className={`relative flex h-9 min-w-0 items-center justify-center gap-2 rounded-lg px-2 text-xs font-semibold tracking-normal transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                      isActive
                        ? 'text-white bg-[#1e232d] border border-white/10'
                        : 'text-zinc-300 hover:text-white hover:bg-white/5'
                    }`}
                  >
                    <Icon size={15} className={`shrink-0 ${isActive ? 'text-emerald-400' : 'text-zinc-400'}`} />
                    <span className="min-w-0 truncate">{link.label}</span>
                  </Link>
                );
              })}
          </div>
        </nav>

        {/* Right Side Buttons (Desktop) */}
        <div className="hidden items-center justify-end space-x-2 md:flex shrink-0">
          {user && (
            <Button
              size="sm"
              asChild
              className="bg-emerald-700 hover:bg-emerald-600 text-white font-bold px-3 rounded-lg"
            >
              <Link href="/new" prefetch={false}>
                Deploy
              </Link>
            </Button>
          )}

          <div className="w-px h-6 bg-white/10 mx-1" aria-hidden="true" />

          <button
            type="button"
            onClick={openSearchPalette}
            aria-label="Search pages and commands"
            className="flex items-center gap-2 h-9 rounded-lg border border-white/15 bg-white/5 px-3 text-sm text-zinc-200 hover:bg-white/10 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
            <span className="hidden lg:inline">Search</span>
            <kbd className="pointer-events-none hidden select-none rounded border border-white/15 bg-white/5 px-1.5 py-0.5 font-mono text-[11px] font-medium text-zinc-300 lg:inline">
              ⌘K
            </kbd>
          </button>

          {user && <NotificationsDropdown />}
          <ModeToggle />

          {user ? (
            <DropdownMenu open={isUserMenuOpen} onOpenChange={setIsUserMenuOpen}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  className="relative h-9 w-9 rounded-full overflow-hidden border border-white/15 text-zinc-100 hover:bg-white/10 p-0"
                  aria-label="User menu"
                >
                  <span className="w-full h-full bg-zinc-800 flex items-center justify-center text-zinc-100 font-semibold">
                    {user.email?.[0]?.toUpperCase() || 'U'}
                  </span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                sideOffset={8}
                className="w-56 bg-card border-border text-card-foreground"
              >
                <DropdownMenuLabel className="font-normal">
                  <div className="flex flex-col space-y-0.5">
                    <p className="text-sm font-medium">My Account</p>
                    <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => router.push('/settings')}
                  className="cursor-pointer gap-2"
                >
                  <Settings size={14} aria-hidden="true" /> Settings
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={handleLogout}
                  className="cursor-pointer gap-2 text-red-500 focus:bg-red-500/10 focus:text-red-500"
                >
                  <LogOut size={14} aria-hidden="true" /> Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" asChild className="text-zinc-200 hover:text-white hover:bg-white/10">
                <Link href="/login" prefetch={false}>
                  Login
                </Link>
              </Button>
              <Button size="sm" asChild className="bg-emerald-700 hover:bg-emerald-600 text-white font-semibold rounded-lg">
                <Link href="/register" prefetch={false}>
                  Get Started
                </Link>
              </Button>
            </div>
          )}
        </div>

        {/* Mobile Menu Button */}
        <div className="flex md:hidden items-center justify-end gap-2 col-start-3">
          <button
            type="button"
            onClick={openSearchPalette}
            aria-label="Search pages and commands"
            className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] rounded-md text-zinc-200 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <Search size={20} aria-hidden="true" />
          </button>
          <ModeToggle />
          <button
            type="button"
            className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] text-zinc-100 rounded-md hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            onClick={() => setIsMenuOpen(!isMenuOpen)}
            aria-label={isMenuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={isMenuOpen}
            aria-controls="mobile-menu"
          >
            {isMenuOpen ? <X size={22} aria-hidden="true" /> : <Menu size={22} aria-hidden="true" />}
          </button>
        </div>
      </div>

      {/* Row 2+3: Infrastructure & Tools — single container, divided */}
      {user && (secondaryAuthLinks.length > 0 || tertiaryAuthLinks.length > 0) && (
        <div className="hidden md:block border-t border-white/10 bg-[#0a0c10]/90 backdrop-blur-md">
          <div className="w-full px-4 sm:px-6 lg:px-8 py-1 max-w-[2200px] mx-auto">
            <nav aria-label="Infrastructure navigation" className="overflow-x-auto scrollbar-hide py-1">
              <div className="mx-auto flex w-max items-center gap-1">
                <span className="text-[11px] uppercase tracking-wider text-zinc-400 font-bold mr-3 shrink-0">Infra</span>
                {secondaryAuthLinks.map((link) => {
                  const Icon = link.icon;
                  const isActive = pathname === link.href;
                  return (
                    <Link
                      key={link.href}
                      href={link.href}
                      prefetch={false}
                      className={`shrink-0 px-2.5 lg:px-3 py-1 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 lg:gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                        isActive
                          ? 'bg-emerald-500/15 text-emerald-400'
                          : 'text-zinc-300 hover:text-white hover:bg-white/5'
                      }`}
                    >
                      <Icon size={12} className={isActive ? 'text-emerald-400' : 'text-zinc-400'} />
                      {link.label}
                    </Link>
                  );
                })}
              </div>
            </nav>
            <div className="border-t border-white/10 mx-auto max-w-2xl" />
            <nav aria-label="Tools navigation" className="overflow-x-auto scrollbar-hide py-1">
              <div className="mx-auto flex w-max items-center gap-1">
                <span className="text-[11px] uppercase tracking-wider text-zinc-400 font-bold mr-3 shrink-0">Tools</span>
                {tertiaryAuthLinks.map((link) => {
                  const Icon = link.icon;
                  const isActive = pathname === link.href;
                  return (
                    <Link
                      key={link.href}
                      href={link.href}
                      prefetch={false}
                      className={`shrink-0 px-2.5 lg:px-3 py-1 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 lg:gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                        isActive
                          ? 'bg-emerald-500/15 text-emerald-400'
                          : 'text-zinc-300 hover:text-white hover:bg-white/5'
                      }`}
                    >
                      <Icon size={12} className={isActive ? 'text-emerald-400' : 'text-zinc-400'} />
                      {link.label}
                    </Link>
                  );
                })}
              </div>
            </nav>
          </div>
        </div>
      )}

      {/* Mobile Menu Dropdown */}
      <AnimatePresence>
        {isMenuOpen && (
          <m.div
            id="mobile-menu"
            initial={prefersReducedMotion ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, height: 0 }}
            className="md:hidden bg-[#0a0c10] text-zinc-100 border-b border-white/10 overflow-hidden"
          >
            <div className="p-4 space-y-4">
              {user ? (
                <nav aria-label="Mobile dashboard navigation" className="flex flex-col space-y-1">
                  {visibleAuthLinks.map((link) => (
                    <Link
                      key={link.href}
                      href={link.href}
                      prefetch={false}
                      className={`flex items-center gap-3 min-h-[44px] px-3 py-2.5 rounded-lg font-medium ${
                        pathname === link.href ? 'bg-emerald-500/15 text-emerald-400' : 'text-zinc-200 hover:bg-white/5'
                      }`}
                      onClick={() => setIsMenuOpen(false)}
                    >
                      <link.icon size={18} /> {link.label}
                    </Link>
                  ))}
                </nav>
              ) : (
                <nav aria-label="Mobile public navigation" className="flex flex-col space-y-1">
                  {publicLinks.map((link) => (
                    <Link
                      key={link.href}
                      href={link.href}
                      prefetch={false}
                      className={`flex items-center min-h-[44px] px-3 py-2.5 rounded-lg font-medium ${
                        pathname === link.href ? 'bg-emerald-500/15 text-emerald-400' : 'text-zinc-200 hover:bg-white/5'
                      }`}
                      onClick={() => setIsMenuOpen(false)}
                    >
                      {link.label}
                    </Link>
                  ))}
                </nav>
              )}

              <div className="pt-4 border-t border-white/10 flex flex-col gap-3">
                {user ? (
                  <>
                    <Button asChild className="w-full bg-emerald-700 hover:bg-emerald-600 text-white font-bold h-11">
                      <Link href="/new" prefetch={false} onClick={() => setIsMenuOpen(false)}>
                        Deploy Project
                      </Link>
                    </Button>
                    <Button
                      variant="destructive"
                      className="w-full justify-start h-11"
                      onClick={() => {
                        handleLogout();
                        setIsMenuOpen(false);
                      }}
                    >
                      <LogOut className="mr-2 h-4 w-4" aria-hidden="true" /> Log out
                    </Button>
                  </>
                ) : (
                  <div className="flex flex-col gap-2">
                    <Button asChild className="w-full bg-emerald-700 hover:bg-emerald-600 text-white font-bold h-11">
                      <Link href="/register" prefetch={false} onClick={() => setIsMenuOpen(false)}>
                        Get Started
                      </Link>
                    </Button>
                    <Button variant="outline" asChild className="w-full h-11 border-white/20 text-white hover:bg-white/10">
                      <Link href="/login" prefetch={false} onClick={() => setIsMenuOpen(false)}>
                        Login
                      </Link>
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </header>
  );
}
