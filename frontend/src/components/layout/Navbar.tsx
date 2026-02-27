'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Settings, Menu, X, Home, LogOut, Rocket, CreditCard, Sparkles, Monitor, Radio, Brain, Archive, Shield, Layout, FolderKanban, Activity, Zap, Gauge, Network, FileCode, ArrowLeftRight, Mail, Play, GitCompare, Users } from 'lucide-react';
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
    { href: '/compare', label: 'Compare' },
    { href: '/docs', label: 'Docs' },
    { href: '/status', label: 'Status' },
    { href: '/contact', label: 'Contact' },
    { href: '/get-started', label: 'Get Started' },
    { href: '/reseller', label: 'Reseller' },
  ];

  const authLinks: Array<{
    href: string;
    label: string;
    icon: any;
    tier: 'primary' | 'secondary';
  }> = [
    { href: '/dashboard', label: 'Dashboard', icon: Home, tier: 'primary' },
    { href: '/projects', label: 'Projects', icon: FolderKanban, tier: 'primary' },
    { href: '/services', label: 'Services', icon: Layout, tier: 'primary' },
    { href: '/deployments', label: 'Deployments', icon: Rocket, tier: 'primary' },
    { href: '/ecosystem', label: 'Ecosystem', icon: Sparkles, tier: 'primary' },
    { href: '/intelligence', label: 'Intelligence', icon: Brain, tier: 'primary' },
    { href: '/activity', label: 'Activity', icon: Activity, tier: 'secondary' },
    { href: '/functions', label: 'Functions', icon: Zap, tier: 'secondary' },
    { href: '/autoscaler', label: 'Autoscaler', icon: Gauge, tier: 'secondary' },
    { href: '/topology', label: 'Topology', icon: Network, tier: 'secondary' },
    { href: '/servers', label: 'Servers', icon: Monitor, tier: 'secondary' },
    { href: '/tunnels', label: 'Tunnels', icon: Radio, tier: 'secondary' },
    { href: '/templates', label: 'Templates', icon: FileCode, tier: 'secondary' },
    { href: '/transfers', label: 'Transfers', icon: ArrowLeftRight, tier: 'secondary' },
    { href: '/billing', label: 'Billing', icon: CreditCard, tier: 'secondary' },
    { href: '/settings', label: 'Settings', icon: Settings, tier: 'secondary' },
  ];

  if (user?.is_staff) {
    authLinks.push({ href: '/backups', label: 'Backups', icon: Archive, tier: 'secondary' });
    authLinks.push({ href: '/admin-dashboard', label: 'Admin', icon: Shield, tier: 'secondary' });
  }

  const primaryAuthLinks = authLinks.filter((link) => link.tier === 'primary');
  const secondaryAuthLinks = authLinks.filter((link) => link.tier === 'secondary');

  return (
    <nav
        className={`sticky top-0 z-50 w-full transition-all duration-300 backdrop-blur-md ${
            isScrolled
            ? 'bg-white/30 dark:bg-slate-950/30 shadow-sm border-b border-white/20'
            : 'bg-transparent border-b border-transparent'
        }`}
    >
      <div className="container mx-auto grid h-16 max-w-7xl grid-cols-[auto_1fr_auto] items-center gap-3 px-4 sm:px-6">

        {/* Logo - Left */}
        <Link href={user ? '/dashboard' : '/'} prefetch={false} className="flex items-center group flex-shrink-0 gap-3">
            <Image src="/images/logo.svg" alt="SMSLY Hosting Logo" width={32} height={32} className="h-8 w-8 shadow-sm rounded-lg" priority />
            {!user && <span className="font-bold text-xl tracking-tight text-slate-900 dark:text-white hidden sm:block">SMSLY Hosting</span>}
        </Link>

        {/* Nav Links - Center: Show public when logged out, auth when logged in */}
        <nav className="hidden min-w-0 items-center justify-center md:flex">
            <div className="flex max-w-full items-center justify-center gap-1 overflow-x-auto whitespace-nowrap [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {!user && publicLinks.map((link) => {
                const isActive = pathname === link.href;
                return (
                     <Link
                         key={link.href}
                         href={link.href}
                         prefetch={false}
                         className={`
                             shrink-0 px-3 py-2 rounded-md text-sm font-medium transition-colors
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
                         className={`
                             relative shrink-0 px-3 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2
                             ${isActive
                                ? 'text-primary bg-primary/5'
                                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'}
                        `}
                    >
                        <Icon size={16} />
                        {link.label}
                        {isActive && (
                            <motion.div
                                layoutId="navbar-indicator"
                                className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary rounded-full"
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

      {user && secondaryAuthLinks.length > 0 && (
        <div className="hidden md:block border-t border-border/60 bg-card/70">
          <div className="container max-w-7xl mx-auto px-4 sm:px-6 py-1.5">
            <div className="flex items-center justify-center gap-1 overflow-x-auto whitespace-nowrap [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {secondaryAuthLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    prefetch={false}
                    className={`shrink-0 px-3 py-1.5 rounded-md text-xs font-medium transition-colors flex items-center gap-1.5 ${
                      isActive
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                    }`}
                  >
                    <Icon size={13} />
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
