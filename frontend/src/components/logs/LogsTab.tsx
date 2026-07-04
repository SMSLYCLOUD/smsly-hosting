import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { Terminal, Zap, Clock, RefreshCw, Radio, Copy, Check } from 'lucide-react';
import { Deployment } from '@/lib/api';
import { getWsUrl } from '@/lib/websocket';
import { PipelineVisualizer, PipelineStage } from '@/components/deployments/PipelineVisualizer';
import { useToast } from '@/components/ui/use-toast';

/**
 * Generate pseudo-timestamps for log lines based on deployment start time.
 */
function addTimestamps(logs: string, startTime: string | null, durationSeconds: number | null): string[] {
    const lines = logs.split('\n');
    if (!startTime) return lines.map(l => l);

    const start = new Date(startTime).getTime();
    const totalDuration = (durationSeconds || 60) * 1000;

    return lines.map((line, i) => {
        const offset = lines.length > 1 ? (i / (lines.length - 1)) * totalDuration : 0;
        const ts = new Date(start + offset);
        const timeStr = ts.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        return `${timeStr}  ${line}`;
    });
}

type LogFilterType = 'ALL' | 'SYSTEM' | 'APP' | 'WARNING' | 'ERROR' | 'NOISE';

function classifyLogLine(line: string): Exclude<LogFilterType, 'ALL'> {
    const lower = line.toLowerCase();
    if (/\berror\b|\bfatal\b|\bpanic\b|\btraceback\b|\bexception\b/.test(lower)) return 'ERROR';
    if (/\bwarn\b|\bdeprecated\b|\bslow\b|\bretry\b/.test(lower)) return 'WARNING';
    if (/\bhealth(check)?\b|\bheartbeat\b|\bping\b|\bpong\b|\bkeepalive\b|\bmetrics?\b/.test(lower)) return 'NOISE';
    if (/\bsystemd\b|\bkernel\b|\bdocker\b|\bcontainerd\b|\btraefik\b|\bnginx\b|\bpostgres\b|\bredis\b/.test(lower)) return 'SYSTEM';
    return 'APP';
}

function matchesFilter(line: string, filter: LogFilterType): boolean {
    if (filter === 'ALL') return true;
    return classifyLogLine(line) === filter;
}

export function LogsTab({ deployment }: { deployment: Deployment | null }) {
    const { toast } = useToast();
    const [logType, setLogType] = useState<'BUILD' | 'RUNTIME'>('BUILD');
    const [logFilter, setLogFilter] = useState<LogFilterType>('ALL');
    const [runtimeLogs, setRuntimeLogs] = useState<string>('');
    const [runtimeLoading, setRuntimeLoading] = useState(false);
    const [runtimeMessage, setRuntimeMessage] = useState('');
    const [liveBuildLogs, setLiveBuildLogs] = useState<string>('');
    const [pipelineStages, setPipelineStages] = useState<PipelineStage[]>([]);
    const [wsConnected, setWsConnected] = useState(false);
    const [isLive, setIsLive] = useState(false);
    const [copied, setCopied] = useState(false);
    const logsEndRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

    // Auto-scroll on new logs
    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [deployment?.build_logs, runtimeLogs, liveBuildLogs]);

    // Initial load of stages
    useEffect(() => {
        if (deployment?.pipeline_stages) {
            setPipelineStages(deployment.pipeline_stages);
        }
    }, [deployment?.pipeline_stages]);

    // Determine if build is still in progress
    const isBuilding = deployment?.status === 'BUILDING' || deployment?.status === 'QUEUED' || deployment?.status === 'PENDING';

    // WebSocket connection for live build logs
    const connectWebSocket = useCallback(() => {
        if (!deployment?.id) return;
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        // Auth for the build-logs WebSocket is provided by the
        // HttpOnly auth cookie that the browser attaches to the
        // WebSocket upgrade request. The server's
        // QueryStringAuthMiddleware reads the cookie directly from
        // the Cookie header (no token in the query string) — see
        // backend/apps/deployments/middleware.py for the matching
        // server-side change.
        const wsUrl = getWsUrl(`/ws/build-logs/${deployment.id}/`);

        try {
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                setWsConnected(true);
                setIsLive(true);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'initial_state') {
                        if (data.build_logs) {
                            setLiveBuildLogs(data.build_logs);
                        }
                        if (data.stages) {
                            setPipelineStages(data.stages);
                        }
                    } else if (data.type === 'build_log') {
                        setLiveBuildLogs(prev => prev + (data.log || ''));
                    } else if (data.type === 'pipeline_update') {
                        setPipelineStages(data.stages);
                    } else if (data.type === 'status_change') {
                        // Build finished, stop live streaming
                        if (data.status === 'ACTIVE' || data.status === 'FAILED') {
                            setIsLive(false);
                        }
                    }
                } catch {
                    // Non-JSON message
                }
            };

            ws.onclose = () => {
                setWsConnected(false);
                // Reconnect if still building
                if (isBuilding) {
                    reconnectTimer.current = setTimeout(connectWebSocket, 3000);
                }
            };

            ws.onerror = () => {
                ws.close();
            };

            wsRef.current = ws;
        } catch {
            // WebSocket not supported or connection failed
        }
    }, [deployment?.id, isBuilding]);

    // Connect WebSocket when viewing build logs during active build
    useEffect(() => {
        if (logType === 'BUILD' && isBuilding && deployment?.id) {
            connectWebSocket();
        }

        return () => {
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
            }
        };
    }, [logType, isBuilding, deployment?.id, connectWebSocket]);

    // Clean up WebSocket on unmount
    useEffect(() => {
        return () => {
            wsRef.current?.close();
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
            }
        };
    }, []);

    // Poll build logs during active build (fallback for when WS isn't available)
    useEffect(() => {
        if (logType !== 'BUILD' || !isBuilding || !deployment?.id) return;
        if (wsConnected) return; // Don't poll if WS is connected

        const fetchBuildLogs = async () => {
            try {
                const res = await fetch(`/api/v1/deployments/${deployment.id}/`, {
                    credentials: "include",
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.build_logs) {
                        setLiveBuildLogs(data.build_logs);
                    }
                }
            } catch {
                // Silently fail
            }
        };

        fetchBuildLogs();
        const interval = setInterval(fetchBuildLogs, 3000);
        return () => clearInterval(interval);
    }, [logType, isBuilding, deployment?.id, wsConnected]);

    const timestampedLogs = useMemo(() => {
        const logs = (isBuilding && liveBuildLogs) ? liveBuildLogs : (deployment?.build_logs || '');
        if (!logs) return [];
        return addTimestamps(
            logs,
            deployment?.created_at || null,
            deployment?.duration_seconds || null
        );
    }, [deployment?.build_logs, deployment?.created_at, deployment?.duration_seconds, liveBuildLogs, isBuilding]);

    // Fetch runtime logs when tab is active
    useEffect(() => {
        if (logType !== 'RUNTIME' || !deployment?.id) return;

        const fetchRuntimeLogs = async () => {
            setRuntimeLoading(true);
            try {
                const res = await fetch(`/api/v1/deployments/${deployment.id}/runtime-logs/?tail=200`, {
                    credentials: "include",
                });
                if (res.ok) {
                    const data = await res.json();
                    setRuntimeLogs(data.runtime_logs || '');
                    setRuntimeMessage(data.message || '');
                } else {
                    setRuntimeMessage('Failed to fetch runtime logs.');
                }
            } catch {
                setRuntimeMessage('Could not connect to the API.');
            } finally {
                setRuntimeLoading(false);
            }
        };

        fetchRuntimeLogs();
        const interval = setInterval(fetchRuntimeLogs, 3000);
        return () => clearInterval(interval);
    }, [logType, deployment?.id]);

    const runtimeLines = runtimeLogs ? runtimeLogs.split('\n') : [];
    const filteredBuildLines = timestampedLogs.filter((line) => matchesFilter(line, logFilter));
    const filteredRuntimeLines = runtimeLines.filter((line) => matchesFilter(line, logFilter));

    const copyVisibleLogs = async () => {
        const visibleLines = logType === 'BUILD' ? filteredBuildLines : filteredRuntimeLines;
        const content = visibleLines.join('\n');
        if (!content) {
            toast({ title: 'No logs to copy' });
            return;
        }
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
            toast({ title: 'Logs copied', description: `Copied ${visibleLines.length} filtered lines.` });
            setTimeout(() => setCopied(false), 1200);
        } catch {
            toast({ title: 'Failed to copy logs', variant: 'destructive' });
        }
    };

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
                    <select
                        value={logFilter}
                        onChange={(e) => setLogFilter(e.target.value as LogFilterType)}
                        className="bg-black/40 border border-white/10 rounded px-2 py-1 text-[11px] text-zinc-300"
                        title="Filter logs by type"
                    >
                        <option value="ALL">All</option>
                        <option value="APP">App</option>
                        <option value="SYSTEM">System</option>
                        <option value="WARNING">Warning</option>
                        <option value="ERROR">Error</option>
                        <option value="NOISE">Noise</option>
                    </select>
                    <button
                        onClick={copyVisibleLogs}
                        className="inline-flex items-center gap-1 rounded border border-white/10 bg-black/40 px-2 py-1 text-[11px] text-zinc-300 hover:bg-black/60"
                        title="Copy visible logs"
                    >
                        {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
                        Copy
                    </button>
                    {deployment?.created_at && (
                        <span className="flex items-center gap-1">
                            <Clock size={10} />
                            {new Date(deployment.created_at).toLocaleString()}
                        </span>
                    )}
                    {logType === 'BUILD' && isBuilding && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                            <span className="text-green-400 font-bold">LIVE</span>
                            {wsConnected && <Radio size={10} className="text-green-500" />}
                        </>
                    )}
                    {logType === 'BUILD' && !isBuilding && deployment?.build_logs && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-green-500"></span>
                            Build Logs
                        </>
                    )}
                    {logType === 'RUNTIME' && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
                            <span className="text-blue-400 font-bold">LIVE</span>
                            <RefreshCw size={10} className="animate-spin text-blue-400" />
                        </>
                    )}
                </div>
            </div>

            {/* Deployment Info Bar */}
            {deployment && (
                <div className="bg-white/[0.02] px-6 py-2 border-b border-white/5 flex items-center gap-4 text-[10px] text-zinc-500 font-sans uppercase tracking-wider">
                    <span>Commit: <span className="text-zinc-300 font-mono">{deployment.commit_hash?.substring(0, 7)}</span></span>
                    <span>Status: <span className={
                        deployment.status === 'ACTIVE' ? 'text-emerald-400' :
                        deployment.status === 'FAILED' ? 'text-red-400' :
                        deployment.status === 'BUILDING' ? 'text-yellow-400 animate-pulse' :
                        'text-zinc-300'
                    }>{deployment.status}</span></span>
                    {deployment.duration_seconds && <span>Duration: <span className="text-zinc-300">{deployment.duration_seconds.toFixed(1)}s</span></span>}
                </div>
            )}

            {/* Content */}
            <div className="flex-1 p-6 overflow-y-auto text-zinc-300 leading-relaxed custom-scrollbar">
                {logType === 'BUILD' && (
                    <>
                        {pipelineStages.length > 0 && (
                            <div className="mb-8 px-4">
                                <PipelineVisualizer stages={pipelineStages} />
                            </div>
                        )}

                        {deployment?.ai_diagnosis && (
                            <div className="bg-emerald-500/10 border-l-2 border-emerald-500 p-4 mb-6 text-emerald-200 rounded-r-lg">
                                <strong className="flex items-center gap-2 mb-2 text-emerald-400 font-sans uppercase tracking-wider text-[10px]">
                                    <Zap size={12} /> AI Insight
                                </strong>
                                {deployment.ai_diagnosis}
                            </div>
                        )}
                        <div className="whitespace-pre-wrap font-mono">
                            {filteredBuildLines.length > 0 ? filteredBuildLines.map((line, i) => (
                                <div key={i} className="hover:bg-white/[0.02] py-px">
                                    <span className="text-zinc-600 select-none">{line.substring(0, 10)}</span>
                                    <span className="text-zinc-400">{line.substring(10)}</span>
                                </div>
                            )) : <span className="text-zinc-600">No logs match the selected filter.</span>}
                        </div>
                        {isBuilding && (
                            <div className="flex items-center gap-2 mt-4 text-yellow-500/80">
                                <RefreshCw size={12} className="animate-spin" />
                                <span className="text-xs font-sans">Build in progress... logs updating live</span>
                            </div>
                        )}
                    </>
                )}

                {logType === 'RUNTIME' && (
                    <>
                        {runtimeLoading && !runtimeLogs && (
                            <div className="flex items-center gap-2 text-zinc-500">
                                <RefreshCw size={14} className="animate-spin" />
                                Fetching container logs...
                            </div>
                        )}
                        {runtimeMessage && !runtimeLogs && (
                            <div className="flex flex-col items-center justify-center h-full text-center gap-3">
                                <Terminal className="h-8 w-8 text-zinc-600" />
                                <p className="text-zinc-500 font-sans text-sm">{runtimeMessage}</p>
                            </div>
                        )}
                        {filteredRuntimeLines.length > 0 && (
                            <div className="whitespace-pre-wrap font-mono">
                                {filteredRuntimeLines.map((line, i) => (
                                    <div key={i} className="hover:bg-white/[0.02] py-px text-zinc-400">
                                        {line}
                                    </div>
                                ))}
                            </div>
                        )}
                        {!runtimeLoading && runtimeLines.length > 0 && filteredRuntimeLines.length === 0 && (
                            <span className="text-zinc-600">No logs match the selected filter.</span>
                        )}
                        <div className="flex items-center gap-2 mt-4 text-blue-500/80">
                            <RefreshCw size={12} className="animate-spin" />
                            <span className="text-xs font-sans">Auto-refreshing every 3s</span>
                        </div>
                    </>
                )}
                <div ref={logsEndRef} />
            </div>
        </div>
    );
}
