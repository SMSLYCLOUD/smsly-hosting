'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
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
  Plug, Settings, FolderKanban, Sun, LogOut, CreditCard,
} from 'lucide-react';
import { featureFlags } from '@/lib/featureFlags';
import { logout as performLogout } from '@/lib/auth';

const navItems = [
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

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  const navigate = React.useCallback(
    (href: string) => {
      setOpen(false);
      router.push(href);
    },
    [router],
  );

  const toggleTheme = React.useCallback(() => {
    setOpen(false);
    const root = document.documentElement;
    const current = root.classList.contains('dark') ? 'dark' : 'light';
    root.classList.toggle('dark', current === 'light');
    root.classList.toggle('light', current === 'dark');
    localStorage.setItem('theme', current === 'dark' ? 'light' : 'dark');
  }, []);

  const filteredNav = navItems.filter((item) => !hiddenByFlag.has(item.href));

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Type a command or search..." />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>

        <CommandGroup heading="Navigation">
          {filteredNav.map((item) => (
            <CommandItem
              key={item.href}
              value={item.label}
              onSelect={() => navigate(item.href)}
            >
              <item.icon className="mr-2 h-4 w-4" />
              <span>{item.label}</span>
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Actions">
          <CommandItem value="theme" onSelect={toggleTheme}>
            <Sun className="mr-2 h-4 w-4" />
            <span>Toggle Theme</span>
          </CommandItem>
          <CommandItem value="billing" onSelect={() => navigate('/settings')}>
            <CreditCard className="mr-2 h-4 w-4" />
            <span>Billing &amp; Settings</span>
          </CommandItem>
          <CommandItem
            value="logout"
            onSelect={() => {
              setOpen(false);
              performLogout();
            }}
          >
            <LogOut className="mr-2 h-4 w-4" />
            <span>Log Out</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
