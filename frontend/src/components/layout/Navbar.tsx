import Link from 'next/link';
import { Box, Settings, Layout, Globe, Menu } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/ui/mode-toggle';

export function Navbar() {
  return (
    <nav className="sticky top-0 z-50 w-full border-b bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/60 transition-colors duration-500">
      <div className="container flex h-16 items-center max-w-7xl mx-auto px-6">
        <div className="mr-4 hidden md:flex">
          <Link href="/" className="mr-6 flex items-center space-x-2">
            <span className="hidden font-bold sm:inline-block text-lg tracking-tight">
              SMSly Hosting
            </span>
          </Link>
          <nav className="flex items-center space-x-6 text-sm font-medium">
            <Link href="/services" className="transition-colors hover:text-foreground/80 text-foreground/60 flex items-center gap-2">
                <Layout size={16} /> Canvas
            </Link>
            <Link href="/store" className="transition-colors hover:text-foreground/80 text-foreground/60 flex items-center gap-2">
                <Box size={16} /> Store
            </Link>
            <Link href="/topology" className="transition-colors hover:text-foreground/80 text-foreground/60 flex items-center gap-2">
                <Globe size={16} /> Topology
            </Link>
          </nav>
        </div>

        <div className="flex flex-1 items-center justify-between space-x-2 md:justify-end">
          <nav className="flex items-center gap-4">
            <Link href="/get-started">
                <Button size="sm" className="bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-600 hover:to-cyan-600 text-white font-bold shadow-lg shadow-emerald-500/20 transition-all hover:scale-105">
                    Deploy Project
                </Button>
            </Link>
            <ModeToggle />
            <Button variant="ghost" size="icon" className="rounded-full">
                <Settings size={18} />
            </Button>
          </nav>
        </div>
      </div>
    </nav>
  );
}
