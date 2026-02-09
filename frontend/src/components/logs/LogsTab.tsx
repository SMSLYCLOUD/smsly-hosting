import React, { useState, useRef, useEffect } from 'react';
import { Terminal, Zap } from 'lucide-react';
import { Deployment } from '@/lib/api';

export function LogsTab({ deployment }: { deployment: Deployment | null }) {
    const [logType, setLogType] = useState<'BUILD' | 'RUNTIME'>('BUILD');
    const logsEndRef = useRef<HTMLDivElement>(null);

    // Mock Runtime Logs
    const [runtimeLogs, setRuntimeLogs] = useState<string>("");
    useEffect(() => {
        if (logType === 'RUNTIME') {
            const interval = setInterval(() => {
                const now = new Date().toISOString();
                const msgs = [
                    `[INFO] ${now} GET /health 200 4ms`,
                    `[INFO] ${now} Worker processing job...`,
                    `[WARN] ${now} Cache miss key=user:123`,
                    `[INFO] ${now} GET /api/v1/services 200 12ms`
                ];
                setRuntimeLogs(prev => prev + msgs[Math.floor(Math.random() * msgs.length)] + '\n');
            }, 1000);
            return () => clearInterval(interval);
        }
    }, [logType]);

    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [deployment?.build_logs, runtimeLogs]);

    return (
        <div className="bg-[#09090b] border border-border rounded-xl overflow-hidden font-mono text-xs h-[700px] flex flex-col shadow-2xl">
            {/* Header / Controls */}
            <div className="bg-white/5 p-3 border-b border-white/10 flex justify-between items-center">
                <div className="flex gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500/80" />
                    <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                    <div className="w-3 h-3 rounded-full bg-green-500/80" />
                </div>
                <div className="flex bg-black/50 rounded-lg p-1 gap-1">
                    <button
                        onClick={() => setLogType('BUILD')}
                        className={`px-3 py-1 rounded text-xs font-bold transition-all ${
                            logType === 'BUILD' ? 'bg-zinc-800 text-white shadow' : 'text-zinc-500 hover:text-zinc-300'
                        }`}
                    >
                        Build
                    </button>
                    <button
                        onClick={() => setLogType('RUNTIME')}
                        className={`px-3 py-1 rounded text-xs font-bold transition-all ${
                            logType === 'RUNTIME' ? 'bg-blue-900/50 text-blue-200 shadow' : 'text-zinc-500 hover:text-zinc-300'
                        }`}
                    >
                        Runtime
                    </button>
                </div>
                <div className="text-zinc-500 font-sans text-xs flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                    Live Tail
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 p-6 overflow-y-auto text-zinc-300 leading-relaxed custom-scrollbar">
                {logType === 'BUILD' && (
                    <>
                        {deployment?.ai_diagnosis && (
                            <div className="bg-emerald-500/10 border-l-2 border-emerald-500 p-4 mb-6 text-emerald-200 rounded-r-lg">
                                <strong className="flex items-center gap-2 mb-2 text-emerald-400 font-sans uppercase tracking-wider text-[10px]">
                                    <Zap size={12} /> AI Insight
                                </strong>
                                {deployment.ai_diagnosis}
                            </div>
                        )}
                        <pre className="whitespace-pre-wrap font-mono text-zinc-400">
                            {deployment?.build_logs || 'Waiting for build logs...'}
                        </pre>
                    </>
                )}

                {logType === 'RUNTIME' && (
                    <pre className="whitespace-pre-wrap font-mono text-emerald-100/90">
                        {runtimeLogs || 'Stream connected. Waiting for output...'}
                    </pre>
                )}
                <div ref={logsEndRef} />
            </div>
        </div>
    );
}
