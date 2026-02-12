'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Box, Settings, Layout, Globe, Menu, X, Home, LogOut, Rocket, CreditCard, FileText, Activity, DollarSign } from 'lucide-react';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/ui/mode-toggle';
import { motion, AnimatePresence } from 'framer-motion';

export function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = React.useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = React.useState(false);
  const [isScrolled, setIsScrolled] = React.useState(false);
  const [user, setUser] = React.useState<{email?: string} | null>(null);
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
        // Mock user data since we don't have a /me endpoint wired yet in this component
        setUser({ email: 'user@smsly.io' });
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
    // Clear both auth cookies to prevent stale session trapping
    document.cookie = 'auth_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;';
    document.cookie = 'sessionid=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;';
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

  const authLinks = [
    { href: '/dashboard', label: 'Dashboard', icon: Home },
    { href: '/services', label: 'Services', icon: Layout },
    { href: '/deployments', label: 'Deployments', icon: Rocket },
    { href: '/topology', label: 'Topology', icon: Globe },
    { href: '/marketplace', label: 'Addons', icon: Box },
    { href: '/billing', label: 'Billing', icon: CreditCard },
    { href: '/settings', label: 'Settings', icon: Settings },
  ];

  return (
    <nav
        className={`sticky top-0 z-50 w-full transition-all duration-300 backdrop-blur-md ${
            isScrolled
            ? 'bg-white/30 dark:bg-slate-950/30 shadow-sm border-b border-white/20'
            : 'bg-transparent border-b border-transparent'
        }`}
    >
      <div className="container flex h-16 items-center max-w-7xl mx-auto px-4 sm:px-6">

        {/* Logo - Left */}
        <Link href="/" className="flex items-center group flex-shrink-0 gap-3">
            <Image src="/images/logo.svg" alt="CloudNeuron Logo" width={32} height={32} className="h-8 w-8 shadow-sm rounded-lg" priority />
            <span className="font-bold text-xl tracking-tight text-slate-900 dark:text-white hidden sm:block">CloudNeuron</span>
        </Link>

        {/* Nav Links - Center: Show public when logged out, auth when logged in */}
        <nav className="hidden md:flex items-center justify-center flex-1 space-x-1">
            {!user && publicLinks.map((link) => {
                const isActive = pathname === link.href;
                return (
                    <Link
                        key={link.href}
                        href={link.href}
                        className={`
                            px-3 py-2 rounded-md text-sm font-medium transition-colors
                            ${isActive
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-slate-700 dark:text-slate-300 hover:text-emerald-600 dark:hover:text-emerald-400'}
                        `}
                    >
                        {link.label}
                    </Link>
                );
            })}
            {user && authLinks.map((link) => {
                const Icon = link.icon;
                const isActive = pathname === link.href;
                return (
                    <Link
                        key={link.href}
                        href={link.href}
                        className={`
                            relative px-3 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2
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
        </nav>

        {/* Right Side Buttons (Desktop) */}
        <div className="hidden md:flex items-center space-x-3">
          {user && (
          <Link href="/new">
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
            <Link href="/login">
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
                        <Link href="/new" onClick={() => setIsMenuOpen(false)}>
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
                            <Link href="/login" onClick={() => setIsMenuOpen(false)}>
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
