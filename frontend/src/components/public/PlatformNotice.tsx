'use client';

import Link from 'next/link';
import { AlertCircle, ArrowLeft, Info, Home, ShieldAlert, Sparkles, Globe, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface PlatformNoticeProps {
  badge?: string;
  title: string;
  message: string;
  secondaryMessage?: string;
  showRetry?: boolean;
  onRetry?: () => void;
}

export default function PlatformNotice({
  badge = 'System Notice',
  title,
  message,
  secondaryMessage,
  showRetry = false,
  onRetry,
}: PlatformNoticeProps) {
  return (
    <main className="min-h-screen bg-gradient-to-br from-[#0a0d1a] via-[#0b1225] to-[#05060d] flex items-center justify-center p-6 relative overflow-hidden font-sans selection:bg-primary/30">
      {/* Lightweight procedural background (no images) */}
      <div
        className="pointer-events-none absolute inset-0 opacity-50"
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 20%, rgba(59,130,246,0.14), transparent 35%), radial-gradient(circle at 80% 10%, rgba(45,212,191,0.10), transparent 30%), radial-gradient(circle at 70% 70%, rgba(168,85,247,0.08), transparent 32%), radial-gradient(circle at 15% 80%, rgba(79,70,229,0.12), transparent 28%)',
        }}
      />

      <section className="relative w-full max-w-3xl z-10 animate-in fade-in slide-in-from-bottom-4 duration-500">
        <div className="relative overflow-hidden rounded-3xl border border-white/8 bg-white/4 backdrop-blur-xl shadow-[0_20px_70px_rgba(0,0,0,0.35)]">
          <div className="absolute inset-x-10 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent" />

          <div className="p-10 sm:p-12">
            <div className="flex flex-col sm:flex-row sm:items-center gap-6 sm:gap-10">
              <div className="flex-1 space-y-4">
                <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-1 text-[10px] font-semibold tracking-[0.22em] uppercase text-primary">
                  <Sparkles className="w-3.5 h-3.5" />
                  {badge}
                </div>
                <h1 className="text-3xl sm:text-4xl md:text-5xl font-black text-white leading-tight">
                  {title}
                </h1>
                <p className="text-base sm:text-lg text-white/80 leading-relaxed">
                  {message}
                </p>
              </div>

              <div className="shrink-0">
                <div className="relative w-20 h-20 sm:w-24 sm:h-24 rounded-3xl bg-gradient-to-br from-amber-400/20 via-amber-500/15 to-orange-400/10 border border-amber-400/30 grid place-items-center shadow-[0_10px_40px_rgba(251,191,36,0.25)]">
                  <ShieldAlert className="w-10 h-10 sm:w-12 sm:h-12 text-amber-300" />
                  <div className="absolute inset-0 rounded-3xl border border-white/10" />
                </div>
              </div>
            </div>

            {secondaryMessage && (
              <div className="mt-8 rounded-2xl border border-white/8 bg-white/3 p-5 sm:p-6 flex gap-3 sm:gap-4">
                <Zap className="w-5 h-5 text-cyan-300 shrink-0 mt-1" />
                <p className="text-sm sm:text-base text-white/70 leading-relaxed">
                  {secondaryMessage}
                </p>
              </div>
            )}

            <div className="mt-10 grid gap-4 sm:grid-cols-2">
              {showRetry && onRetry && (
                <Button
                  onClick={onRetry}
                  className="h-12 rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground font-semibold shadow-[0_10px_30px_rgba(59,130,246,0.4)]"
                >
                  Retry Connection
                </Button>
              )}

              <Link href="/" className="w-full">
                <Button
                  variant="outline"
                  className="w-full h-12 rounded-xl border-white/15 bg-white/5 text-white hover:bg-white/10 hover:border-white/30 font-semibold"
                >
                  <Home className="w-5 h-5 mr-2 text-cyan-300" />
                  Go Back Home
                </Button>
              </Link>
            </div>

            <div className="mt-8 pt-6 border-t border-white/5 flex flex-wrap gap-6 text-xs font-semibold tracking-[0.16em] uppercase text-white/45">
              <Link href="/status" className="hover:text-primary transition-colors flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.7)]" />
                Status
              </Link>
              <Link href="/docs" className="hover:text-cyan-300 transition-colors">
                Documentation
              </Link>
              <Link href="/contact" className="hover:text-purple-300 transition-colors">
                Support
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
