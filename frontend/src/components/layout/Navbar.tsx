'use client';

import * as React from 'react';
import Link from 'next/link';
import { Box, Settings, Layout, Globe, Menu, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/ui/mode-toggle';

export function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = React.useState(false);

  return (
    <nav className="sticky top-0 z-50 w-full border-b bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/60 transition-colors duration-500">
      <div className="container flex h-16 items-center justify-between max-w-7xl mx-auto px-4 sm:px-6">

        {/* Logo & Desktop Menu */}
        <div className="flex items-center">
            <Link href="/" className="mr-6 flex items-center space-x-2">
                <span className="font-bold text-lg tracking-tight">
                SMSly Hosting
                </span>
            </Link>

            <nav className="hidden md:flex items-center space-x-6 text-sm font-medium">
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

        {/* Right Side Buttons (Desktop) */}
        <div className="hidden md:flex items-center space-x-4">
          <Link href="/get-started">
              <Button size="sm" className="bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-600 hover:to-cyan-600 text-white font-bold shadow-lg shadow-emerald-500/20 transition-all hover:scale-105">
                  Deploy Project
              </Button>
          </Link>
          <ModeToggle />
          <Button variant="ghost" size="icon" className="rounded-full">
              <Settings size={18} />
          </Button>
        </div>

        {/* Mobile Menu Button */}
        <div className="flex md:hidden items-center gap-4">
            <ModeToggle />
            <button
                className="text-foreground focus:outline-none"
                onClick={() => setIsMenuOpen(!isMenuOpen)}
            >
                {isMenuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
        </div>
      </div>

      {/* Mobile Menu Dropdown */}
      {isMenuOpen && (
          <div className="md:hidden bg-background border-b border-border p-4 space-y-4 shadow-xl">
            <nav className="flex flex-col space-y-4">
                <Link href="/services" className="text-foreground/80 hover:text-foreground font-medium flex items-center gap-2 p-2 rounded-lg hover:bg-muted" onClick={() => setIsMenuOpen(false)}>
                    <Layout size={18} /> Canvas
                </Link>
                <Link href="/store" className="text-foreground/80 hover:text-foreground font-medium flex items-center gap-2 p-2 rounded-lg hover:bg-muted" onClick={() => setIsMenuOpen(false)}>
                    <Box size={18} /> Store
                </Link>
                <Link href="/topology" className="text-foreground/80 hover:text-foreground font-medium flex items-center gap-2 p-2 rounded-lg hover:bg-muted" onClick={() => setIsMenuOpen(false)}>
                    <Globe size={18} /> Topology
                </Link>
            </nav>
            <div className="pt-4 border-t border-border flex flex-col gap-3">
                <Link href="/get-started" onClick={() => setIsMenuOpen(false)}>
                    <Button className="w-full bg-gradient-to-r from-emerald-500 to-cyan-500 text-white font-bold">
                        Deploy Project
                    </Button>
                </Link>
            </div>
          </div>
      )}
    </nav>
  );
}
