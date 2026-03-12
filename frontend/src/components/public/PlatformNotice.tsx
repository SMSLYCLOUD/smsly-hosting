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
    <main className="min-h-screen bg-[#050510] flex items-center justify-center p-6 relative overflow-hidden font-sans selection:bg-primary/30">
      {/* Dynamic Cosmic Background */}
      <div className="absolute inset-0 z-0">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-primary/20 rounded-full blur-[120px] animate-pulse" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-cyan-500/10 rounded-full blur-[120px] animate-pulse" style={{ animationDelay: '2s' }} />
        <div className="absolute top-[20%] right-[10%] w-[30%] h-[30%] bg-purple-600/10 rounded-full blur-[100px] animate-pulse" style={{ animationDelay: '4s' }} />
        
        {/* Animated Dot Grid */}
        <div className="absolute inset-0 opacity-[0.15]" 
             style={{ 
               backgroundImage: 'radial-gradient(circle, rgba(255,255,255,0.1) 1px, transparent 1px)', 
               backgroundSize: '32px 32px' 
             }} 
        />
      </div>
      
      <section className="relative w-full max-w-2xl z-10 transition-all duration-700 animate-in fade-in zoom-in slide-in-from-bottom-8">
        {/* Glass Container */}
        <div className="relative group">
          {/* Outer Glow */}
          <div className="absolute -inset-1 bg-gradient-to-r from-primary/30 via-cyan-500/30 to-purple-600/30 rounded-[2.5rem] blur-2xl opacity-50 group-hover:opacity-100 transition duration-1000 group-hover:duration-200" />
          
          <div className="relative backdrop-blur-2xl bg-black/40 border border-white/10 rounded-[2.5rem] overflow-hidden shadow-2xl">
            {/* Top Highlight Stripe */}
            <div className="h-1.5 w-full bg-gradient-to-r from-primary via-cyan-400 to-purple-500 shadow-[0_0_20px_rgba(59,130,246,0.5)]" />
            
            <div className="p-8 md:p-14">
              <div className="flex flex-col md:flex-row justify-between items-center md:items-start gap-8 mb-12">
                <div className="flex flex-col items-center md:items-start gap-4">
                  <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-5 py-2 text-[10px] font-black tracking-[0.2em] uppercase text-primary shadow-[0_0_15px_rgba(59,130,246,0.2)]">
                    <Sparkles className="w-3.5 h-3.5 animate-spin-slow" />
                    {badge}
                  </div>
                  <h1 className="text-4xl md:text-5xl font-black tracking-tight text-white leading-[1.1] text-center md:text-left drop-shadow-sm">
                    {title}
                  </h1>
                </div>
                
                <div className="relative">
                  <div className="absolute -inset-4 bg-amber-500/20 rounded-full blur-xl animate-pulse" />
                  <div className="w-20 h-20 rounded-3xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shadow-inner relative z-10">
                    <ShieldAlert className="w-10 h-10 text-amber-500 drop-shadow-[0_0_10px_rgba(245,158,11,0.5)]" />
                  </div>
                </div>
              </div>

              <div className="space-y-6">
                <p className="text-xl text-white/80 leading-relaxed font-medium text-center md:text-left">
                  {message}
                </p>
                
                {secondaryMessage && (
                  <div className="relative overflow-hidden p-6 rounded-2xl bg-white/[0.03] border border-white/5 group-hover:bg-white/[0.05] transition-colors duration-500">
                    <div className="flex gap-4">
                      <Zap className="w-5 h-5 text-cyan-400 shrink-0 mt-1" />
                      <p className="text-[15px] text-white/50 leading-relaxed italic font-medium">
                        {secondaryMessage}
                      </p>
                    </div>
                  </div>
                )}
              </div>

              <div className="mt-14 flex flex-col sm:flex-row gap-5">
                {showRetry && onRetry && (
                  <Button 
                    onClick={onRetry} 
                    className="flex-[1.5] h-14 rounded-2xl bg-primary hover:bg-primary/90 text-primary-foreground shadow-[0_0_25px_rgba(59,130,246,0.4)] hover:shadow-primary/60 transition-all duration-300 text-base font-black uppercase tracking-widest overflow-hidden relative group"
                  >
                    <span className="relative z-10">Retry Connection</span>
                    <div className="absolute inset-0 bg-gradient-to-r from-primary via-white/20 to-primary translate-x-[-100%] group-hover:translate-x-[100%] transition-transform duration-1000 ease-in-out" />
                  </Button>
                )}

                <Link href="/" className="flex-1">
                  <Button variant="outline" className="w-full h-14 rounded-2xl border-white/10 bg-white/5 text-white hover:bg-white/10 hover:border-white/20 font-black uppercase tracking-widest transition-all duration-300 shadow-lg">
                    <Home className="w-5 h-5 mr-3 text-cyan-400" />
                    Go Back Home
                  </Button>
                </Link>
              </div>
              
              <div className="mt-10 pt-10 border-t border-white/5 flex flex-wrap justify-center md:justify-start gap-8">
                 <Link href="/status" className="group/link text-xs font-black uppercase tracking-[0.2em] text-white/40 hover:text-primary transition-all duration-300 flex items-center gap-3">
                   <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] group-hover:scale-125 transition-transform" />
                   Status
                 </Link>
                 <Link href="/docs" className="text-xs font-black uppercase tracking-[0.2em] text-white/40 hover:text-cyan-400 transition-all duration-300">
                   Documentation
                 </Link>
                 <Link href="/contact" className="text-xs font-black uppercase tracking-[0.2em] text-white/40 hover:text-purple-400 transition-all duration-300">
                   Support
                 </Link>
              </div>
            </div>
          </div>
        </div>
        
        {/* Modern Footer Branding */}
        <div className="mt-8 flex flex-col items-center gap-2">
          <div className="flex items-center gap-3 opacity-30">
            <div className="h-px w-8 bg-white/50" />
            <Globe className="w-4 h-4 text-white" />
            <div className="h-px w-8 bg-white/50" />
          </div>
          <p className="text-[10px] font-black uppercase tracking-[0.4em] text-white/20">
            Powered by CloudNeuron <span className="text-primary italic">Neural Engine</span> v4.2
          </p>
        </div>
      </section>
    </main>
  );
}
