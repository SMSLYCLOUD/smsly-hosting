'use client';

import Link from 'next/link';
import { AlertCircle, ArrowLeft, Info, Home, ShieldAlert } from 'lucide-react';
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
    <main className="min-h-screen cloud-bg flex items-center justify-center p-6 relative overflow-hidden">
      {/* Decorative Orbs */}
      <div className="floating-orb w-[400px] h-[400px] bg-primary/10 -top-20 -left-20" />
      <div className="floating-orb w-[300px] h-[300px] bg-cyan-500/10 bottom-20 right-20" style={{ animationDelay: '-4s' }} />
      
      <section className="relative w-full max-w-2xl z-10">
        <div className="card-premium rounded-3xl overflow-hidden dot-grid">
          <div className="h-2 w-full bg-gradient-to-r from-primary via-cyan-400 to-emerald-400" />
          
          <div className="p-10 md:p-12">
            <div className="flex justify-between items-start mb-10">
              <div className="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-4 py-1.5 text-xs font-bold tracking-widest uppercase text-primary">
                <Info className="w-4 h-4" />
                {badge}
              </div>
              
              <div className="w-14 h-14 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center icon-glow shadow-amber-500/20">
                <ShieldAlert className="w-7 h-7 text-amber-500" />
              </div>
            </div>

            <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-foreground mb-6 leading-tight">
              {title}
            </h1>
            
            <div className="space-y-4">
              <p className="text-lg text-foreground/80 leading-relaxed font-medium">
                {message}
              </p>
              {secondaryMessage && (
                <div className="p-4 rounded-xl bg-muted/50 border border-border/50 text-sm text-muted-foreground leading-relaxed italic">
                  {secondaryMessage}
                </div>
              )}
            </div>

            <div className="mt-12 flex flex-col sm:flex-row gap-4">
              {showRetry && onRetry && (
                <Button onClick={onRetry} className="flex-1 h-12 rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground btn-shimmer text-base font-bold">
                  Retry Connection
                </Button>
              )}

              <Link href="/" className="flex-1">
                <Button variant="outline" className="w-full h-12 rounded-xl border-border/80 text-foreground hover:bg-muted font-bold text-base">
                  <Home className="w-5 h-5 mr-2" />
                  CloudNeuron Home
                </Button>
              </Link>
            </div>
            
            <div className="mt-8 pt-8 border-t border-border/30 flex flex-wrap justify-center gap-6">
               <Link href="/status" className="text-sm font-semibold text-muted-foreground hover:text-primary transition-colors flex items-center gap-2">
                 <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                 Platform Status
               </Link>
               <Link href="/docs" className="text-sm font-semibold text-muted-foreground hover:text-primary transition-colors">
                 Help Center
               </Link>
               <Link href="/contact" className="text-sm font-semibold text-muted-foreground hover:text-primary transition-colors">
                 Support
               </Link>
            </div>
          </div>
        </div>
        
        {/* Subtle Footer info */}
        <p className="text-center mt-6 text-xs font-bold uppercase tracking-[0.2em] text-muted-foreground/50">
          Powered by CloudNeuron Neural Engine v4.2
        </p>
      </section>
    </main>
  );
}
