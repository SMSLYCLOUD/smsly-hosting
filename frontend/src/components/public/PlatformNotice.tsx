'use client';

import Link from 'next/link';
import { AlertTriangle, ArrowLeft, Info } from 'lucide-react';
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
  badge = 'Notice',
  title,
  message,
  secondaryMessage,
  showRetry = false,
  onRetry,
}: PlatformNoticeProps) {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <section className="w-full max-w-3xl border border-slate-800 rounded-2xl bg-slate-900/80 backdrop-blur-md overflow-hidden">
        <div className="h-1.5 w-full bg-gradient-to-r from-cyan-500 via-emerald-400 to-amber-400" />

        <div className="p-8 md:p-10">
          <div className="inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-800 px-3 py-1 text-xs font-semibold tracking-wide uppercase text-cyan-300">
            <Info className="w-3.5 h-3.5" />
            {badge}
          </div>

          <div className="mt-6 flex items-start gap-4">
            <div className="w-11 h-11 rounded-xl bg-amber-500/15 border border-amber-500/30 flex items-center justify-center shrink-0">
              <AlertTriangle className="w-5 h-5 text-amber-300" />
            </div>
            <div>
              <h1 className="text-2xl md:text-3xl font-semibold tracking-tight">{title}</h1>
              <p className="mt-3 text-slate-300 leading-relaxed">{message}</p>
              {secondaryMessage ? (
                <p className="mt-2 text-sm text-slate-400">{secondaryMessage}</p>
              ) : null}
            </div>
          </div>

          <div className="mt-8 flex flex-wrap gap-3">
            {showRetry && onRetry ? (
              <Button onClick={onRetry} className="bg-cyan-600 hover:bg-cyan-500 text-white">
                Try Again
              </Button>
            ) : null}

            <Link href="/">
              <Button variant="outline" className="border-slate-700 text-slate-200 hover:bg-slate-800">
                <ArrowLeft className="w-4 h-4 mr-2" />
                Back to Home
              </Button>
            </Link>

            <Link href="/status">
              <Button variant="ghost" className="text-slate-300 hover:bg-slate-800">
                Platform Status
              </Button>
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
