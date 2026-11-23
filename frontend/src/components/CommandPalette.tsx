'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useTheme } from 'next-themes';
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import {
  Home, Layout, Rocket, Sparkles, Brain, Monitor, Gauge, Network,
  GitCompare, Radio, Shield, Activity, Zap, FileCode, ArrowLeftRight,
  Plug, Settings, FolderKanban, Sun, LogOut, CreditCard, BookOpen,
  DollarSign, MessageSquare, LogIn, UserPlus,
} from 'lucide-react';
import { featureFlags } from '@/lib/featureFlags';
import { logout as performLogout } from '@/lib/auth';
import { useAuth } from '@/components/auth-provider';

const publicNavItems = [
  { href: '/', label: 'Home', icon: Home },
  { href: '/compare', label: 'Compare vs Managed PaaS', icon: GitCompare },
  { href: '/store', label: 'Templates', icon: FileCode },
  { href: '/pricing', label: 'Pricing', icon: DollarSign },
  { href: '/docs', label: 'Documentation', icon: BookOpen },
  { href: '/docs/install', label: 'Installation Guide', icon: Rocket },
  { href: '/docs/security', label: 'Security Architecture', icon: Shield },
  { href: '/status', label: 'System Status', icon: Activity },
  { href: '/contact', label: 'Contact & Support', icon: MessageSquare },
];

const authNavItems = [
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

const hiddenByFlag = new Set<string>([
  ...(!featureFlags.autoscaler ? ['/autoscaler'] : []),
  ...(!featureFlags.replication ? ['/replication'] : []),
  ...(!featureFlags.tunnels ? ['/tunnels'] : []),
  ...(!featureFlags.vpnMesh ? ['/network'] : []),
  ...(!featureFlags.functions ? ['/functions'] : []),
  ...(!featureFlags.transfers ? ['/transfers'] : []),
]);

export function CommandPalette() {
  const [open, setOpen] = React.useState(false);
  const router = useRouter();
  const { user, loading } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (!loading) {
          setOpen((prev) => !prev);
        }
      }
    };
    const handleCustomOpen = () => {
      if (!loading) setOpen(true);
    };
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('smsly:open-command-palette', handleCustomOpen);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('smsly:open-command-palette', handleCustomOpen);
    };
  }, [loading]);

  const navigate = React.useCallback(
    (href: string) => {
      setOpen(false);
      router.push(href);
    },
    [router],
  );

  const toggleTheme = React.useCallback(() => {
    setOpen(false);
    setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');
  }, [resolvedTheme, setTheme]);

  const filteredAuthNav = authNavItems.filter((item) => !hiddenByFlag.has(item.href));

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Type a page or command..." aria-label="Search pages and commands" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>

        <CommandGroup heading={user ? 'Dashboard Navigation' : 'Pages & Documentation'}>
          {(user ? filteredAuthNav : publicNavItems).map((item) => (
            <CommandItem
              key={item.href}
              value={item.label}
              onSelect={() => navigate(item.href)}
            >
              <item.icon className="mr-2 h-4 w-4" aria-hidden="true" />
              <span>{item.label}</span>
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Actions">
          <CommandItem value="Toggle Theme" onSelect={toggleTheme}>
            <Sun className="mr-2 h-4 w-4" aria-hidden="true" />
            <span>Toggle Theme</span>
          </CommandItem>
          {user ? (
            <>
              <CommandItem value="Billing and Settings" onSelect={() => navigate('/settings')}>
                <CreditCard className="mr-2 h-4 w-4" aria-hidden="true" />
                <span>Billing &amp; Settings</span>
              </CommandItem>
              <CommandItem
                value="Log Out"
                onSelect={() => {
                  setOpen(false);
                  performLogout();
                }}
              >
                <LogOut className="mr-2 h-4 w-4" aria-hidden="true" />
                <span>Log Out</span>
              </CommandItem>
            </>
          ) : (
            <>
              <CommandItem value="Sign In" onSelect={() => navigate('/login')}>
                <LogIn className="mr-2 h-4 w-4" aria-hidden="true" />
                <span>Sign In</span>
              </CommandItem>
              <CommandItem value="Create Account" onSelect={() => navigate('/register')}>
                <UserPlus className="mr-2 h-4 w-4" aria-hidden="true" />
                <span>Create Account</span>
              </CommandItem>
            </>
          )}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
