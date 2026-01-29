'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Check, Shield, Search, Zap, Loader2, Code2 } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

export default function LivingOnboarding() {
  const router = useRouter();
  const [phase, setPhase] = useState<'IDLE' | 'ANALYZING' | 'PROPOSAL' | 'DEPLOYING'>('IDLE');
  const [repoUrl, setRepoUrl] = useState('');
  const [logs, setLogs] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  const addLog = (msg: string) => {
    setLogs(prev => [...prev, msg]);
    setTimeout(() => scrollRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
  };

  const handleAnalyze = async () => {
    if (!repoUrl) return;
    setPhase('ANALYZING');
    addLog('Connecting to GitHub...');
    await new Promise(r => setTimeout(r, 800));
    addLog('Cloning repository structure...');
    await new Promise(r => setTimeout(r, 600));
    addLog('Scanning for Dockerfile... Found.');
    await new Promise(r => setTimeout(r, 600));
    addLog('Scanning requirements.txt... Found Django.');
    await new Promise(r => setTimeout(r, 600));
    addLog('Checking for exposed secrets... Clean.');
    await new Promise(r => setTimeout(r, 800));
    setPhase('PROPOSAL');
  };

  const handleDeploy = async () => {
    setPhase('DEPLOYING');
    addLog('Initializing secure container...');
    await new Promise(r => setTimeout(r, 1000));
    addLog('Injecting SMSly API Keys...');
    await new Promise(r => setTimeout(r, 800));
    addLog('Provisioning PostgreSQL Add-on...');
    await new Promise(r => setTimeout(r, 1200));
    router.push('/services/svc-new-123');
  };

  return (
    <main className="min-h-screen bg-background font-sans flex flex-col">
      <Navbar />

      <div className="flex-1 flex flex-col items-center justify-center p-6 relative overflow-hidden bg-dot-pattern">

        <div className="max-w-3xl w-full z-10 space-y-8">

            {phase === 'IDLE' && (
                <div className="text-center animate-in fade-in zoom-in duration-500">
                    <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-100 text-emerald-800 text-xs font-bold uppercase tracking-wider mb-6 border border-emerald-200">
                        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                        AI Agent Online
                    </div>
                    <h1 className="text-5xl font-extrabold tracking-tight mb-6 text-foreground">
                        What are we shipping?
                    </h1>
                    <Card className="p-2 pl-4 flex items-center gap-2 shadow-xl border-2 border-primary/20 focus-within:border-primary transition-colors bg-white">
                        <Code2 className="text-muted-foreground" />
                        <input
                            type="text"
                            placeholder="github.com/username/repo"
                            className="flex-1 text-lg p-2 outline-none bg-transparent"
                            value={repoUrl}
                            onChange={e => setRepoUrl(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleAnalyze()}
                            autoFocus
                        />
                        <Button size="lg" onClick={handleAnalyze} className="rounded-md">
                            Analyze <ArrowRight className="ml-2 h-4 w-4" />
                        </Button>
                    </Card>
                </div>
            )}

            {(phase === 'ANALYZING' || phase === 'PROPOSAL' || phase === 'DEPLOYING') && (
                <Card className="w-full bg-card border-border shadow-2xl overflow-hidden">
                    <div className="bg-muted/50 p-4 border-b flex items-center justify-between">
                        <div className="flex items-center gap-2 text-sm font-medium">
                            <div className="w-3 h-3 rounded-full bg-red-500" />
                            <div className="w-3 h-3 rounded-full bg-yellow-500" />
                            <div className="w-3 h-3 rounded-full bg-green-500" />
                            <span className="ml-2 text-muted-foreground">agent-terminal</span>
                        </div>
                    </div>

                    <div className="p-8 h-96 flex flex-col">
                        <div className="flex-1 space-y-3 font-mono text-sm overflow-y-auto" ref={scrollRef}>
                            {logs.map((log, i) => (
                                <div key={i} className="flex items-center gap-3 text-foreground/80 animate-in fade-in slide-in-from-left-2">
                                    <span className="text-emerald-500">➜</span>
                                    {log}
                                </div>
                            ))}
                            {(phase === 'ANALYZING' || phase === 'DEPLOYING') && (
                                <div className="flex items-center gap-3 text-muted-foreground animate-pulse">
                                    <span className="text-emerald-500">➜</span>
                                    Processing...
                                </div>
                            )}
                        </div>

                        {phase === 'PROPOSAL' && (
                            <div className="mt-6 pt-6 border-t animate-in slide-in-from-bottom-4">
                                <div className="flex items-center justify-between mb-4">
                                    <div>
                                        <h3 className="font-bold text-lg">Django Application Detected</h3>
                                        <p className="text-sm text-muted-foreground">I recommend provisioning a Postgres database.</p>
                                    </div>
                                    <div className="flex gap-2">
                                        <Button variant="outline" onClick={() => setPhase('IDLE')}>Cancel</Button>
                                        <Button onClick={handleDeploy}>Deploy Stack</Button>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                </Card>
            )}
        </div>
      </div>
    </main>
  );
}
