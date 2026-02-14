import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Terminal, Zap, Clock } from 'lucide-react';
import { Deployment } from '@/lib/api';

/**
 * Generate pseudo-timestamps for log lines based on deployment start time.
 * Since backend stores logs as a single text blob, we approximate
 * timestamps by spreading lines over the deployment duration.
 */
function addTimestamps(logs: string, startTime: string | null, durationSeconds: number | null): string[] {
    const lines = logs.split('\n');
    if (!startTime) return lines.map(l => l);

    const start = new Date(startTime).getTime();
    const totalDuration = (durationSeconds || 60) * 1000; // default to 60s if unknown

    return lines.map((line, i) => {
        const offset = lines.length > 1 ? (i / (lines.length - 1)) * totalDuration : 0;
        const ts = new Date(start + offset);
        const timeStr = ts.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        return `${timeStr}  ${line}`;
    });
}

export function LogsTab({ deployment }: { deployment: Deployment | null }) {
    const [logType, setLogType] = useState<'BUILD' | 'RUNTIME'>('BUILD');
    const logsEndRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [deployment?.build_logs]);

    const timestampedLogs = useMemo(() => {
        if (!deployment?.build_logs) return [];
        return addTimestamps(
            deployment.build_logs,
            deployment.created_at || null,
            deployment.duration_seconds || null
        );
    }, [deployment?.build_logs, deployment?.created_at, deployment?.duration_seconds]);

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
                    {deployment?.created_at && (
                        <span className="flex items-center gap-1">
                            <Clock size={10} />
                            {new Date(deployment.created_at).toLocaleString()}
                        </span>
                    )}
                    {logType === 'BUILD' && deployment?.build_logs && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                            Build Logs
                        </>
                    )}
                </div>
            </div>

            {/* Deployment Info Bar */}
            {deployment && (
                <div className="bg-white/[0.02] px-6 py-2 border-b border-white/5 flex items-center gap-4 text-[10px] text-zinc-500 font-sans uppercase tracking-wider">
                    <span>Commit: <span className="text-zinc-300 font-mono">{deployment.commit_hash?.substring(0, 7)}</span></span>
                    <span>Status: <span className={deployment.status === 'ACTIVE' ? 'text-emerald-400' : deployment.status === 'FAILED' ? 'text-red-400' : 'text-zinc-300'}>{deployment.status}</span></span>
                    {deployment.duration_seconds && <span>Duration: <span className="text-zinc-300">{deployment.duration_seconds.toFixed(1)}s</span></span>}
                </div>
            )}

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
                        <div className="whitespace-pre-wrap font-mono">
                            {timestampedLogs.length > 0 ? timestampedLogs.map((line, i) => (
                                <div key={i} className="hover:bg-white/[0.02] py-px">
                                    <span className="text-zinc-600 select-none">{line.substring(0, 10)}</span>
                                    <span className="text-zinc-400">{line.substring(10)}</span>
                                </div>
                            )) : <span className="text-zinc-600">Waiting for build logs...</span>}
                        </div>
                    </>
                )}

                {logType === 'RUNTIME' && (
                    <div className="flex flex-col items-center justify-center h-full text-center gap-3">
                        <Terminal className="h-8 w-8 text-zinc-600" />
                        <p className="text-zinc-500 font-sans text-sm">Runtime log streaming coming soon</p>
                        <p className="text-zinc-600 font-sans text-xs max-w-sm">
                            Container stdout/stderr streaming requires WebSocket infrastructure.
                            Build logs above show the full deployment output.
                        </p>
                    </div>
                )}
                <div ref={logsEndRef} />
            </div>
        </div>
    );
}

