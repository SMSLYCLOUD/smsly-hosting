import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Terminal, Zap, Clock, RefreshCw, Radio, Copy, Check } from 'lucide-react';
import { Deployment } from '@/lib/api';
import { getWsUrl } from '@/lib/websocket';
import { PipelineVisualizer, PipelineStage } from '@/components/deployments/PipelineVisualizer';
import { useToast } from '@/components/ui/use-toast';
import {
    LiveLogViewer,
    LiveLogViewerHandle,
    LogLine,
} from '@/components/logs/LiveLogViewer';

type LogFilterType = 'ALL' | 'SYSTEM' | 'APP' | 'WARNING' | 'ERROR' | 'NOISE';

export function LogsTab({ deployment }: { deployment: Deployment | null }) {
    const { toast } = useToast();
    const [logType, setLogType] = useState<'BUILD' | 'RUNTIME'>('BUILD');
    const [runtimeMessage, setRuntimeMessage] = useState('');
    const [pipelineStages, setPipelineStages] = useState<PipelineStage[]>([]);
    const [wsConnected, setWsConnected] = useState(false);
    const [copied, setCopied] = useState(false);

    const buildViewerRef = useRef<LiveLogViewerHandle>(null);
    const runtimeViewerRef = useRef<LiveLogViewerHandle>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);
    const seqRef = useRef(0);
    const makeId = useCallback(() => {
        seqRef.current += 1;
        return `${Date.now().toString(36)}-${seqRef.current.toString(36)}`;
    }, []);

    // Initial load of stages
    useEffect(() => {
        if (deployment?.pipeline_stages) {
            setPipelineStages(deployment.pipeline_stages);
        }
    }, [deployment?.pipeline_stages]);

    // Determine if build is still in progress
    const isBuilding = deployment?.status === 'BUILDING' || deployment?.status === 'QUEUED' || deployment?.status === 'PENDING';

    // ---- WebSocket: build logs ----
    const connectBuildWebSocket = useCallback(() => {
        if (!deployment?.id) return;
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const wsUrl = getWsUrl(`/ws/build-logs/${deployment.id}/`);

        try {
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                setWsConnected(true);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'initial_state') {
                        if (data.build_logs) {
                            // Seed the viewer with the full saved log buffer.
                            // We parse it line-by-line and append in one shot.
                            const lines: LogLine[] = data.build_logs
                                .split('\n')
                                .filter((l: string) => l.length > 0)
                                .map((text: string) => ({
                                    id: makeId(),
                                    text,
                                }));
                            buildViewerRef.current?.clear();
                            buildViewerRef.current?.append(lines);
                        }
                        if (data.stages) {
                            setPipelineStages(data.stages);
                        }
                    } else if (data.type === 'build_log') {
                        if (data.log) {
                            buildViewerRef.current?.appendRaw(data.log);
                        }
                    } else if (data.type === 'pipeline_update') {
                        setPipelineStages(data.stages);
                    } else if (data.type === 'status_change') {
                        // Build finished — nothing else to do, the viewer
                        // will stop receiving messages naturally.
                    }
                } catch {
                    // Non-JSON message — ignore
                }
            };

            ws.onclose = () => {
                setWsConnected(false);
                if (isBuilding) {
                    reconnectTimer.current = setTimeout(connectBuildWebSocket, 3000);
                }
            };

            ws.onerror = () => {
                ws.close();
            };

            wsRef.current = ws;
        } catch {
            // WebSocket not supported or connection failed
        }
    }, [deployment?.id, isBuilding, makeId]);

    // ---- WebSocket: runtime logs ----
    const connectRuntimeWebSocket = useCallback(() => {
        if (!deployment?.id) return;
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const wsUrl = getWsUrl(`/ws/runtime-logs/${deployment.id}/`);

        try {
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                setWsConnected(true);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'initial_state') {
                        runtimeViewerRef.current?.clear();
                        if (data.logs) {
                            runtimeViewerRef.current?.appendRaw(data.logs);
                        }
                        setRuntimeMessage(data.message || '');
                        if (data.container_status && data.container_status !== 'running') {
                            setRuntimeMessage(
                                data.source === 'build_logs'
                                    ? 'Container is not running. Showing saved crash logs.'
                                    : `Container status: ${data.container_status}`
                            );
                        }
                    } else if (data.type === 'log') {
                        if (data.log) {
                            runtimeViewerRef.current?.appendRaw(data.log);
                        }
                    } else if (data.type === 'error') {
                        setRuntimeMessage(data.error || 'Stream error');
                    }
                } catch {
                    // Non-JSON message
                }
            };

            ws.onclose = () => {
                setWsConnected(false);
                // Auto-reconnect for runtime logs (container may restart)
                reconnectTimer.current = setTimeout(connectRuntimeWebSocket, 5000);
            };

            ws.onerror = () => {
                ws.close();
            };

            wsRef.current = ws;
        } catch {
            // WebSocket not supported
        }
    }, [deployment?.id, makeId]);

    // Connect WebSocket when viewing build logs during active build
    useEffect(() => {
        if (logType === 'BUILD' && isBuilding && deployment?.id) {
            connectBuildWebSocket();
        }
        return () => {
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
            }
        };
    }, [logType, isBuilding, deployment?.id, connectBuildWebSocket]);

    // Connect WebSocket when viewing runtime logs
    useEffect(() => {
        if (logType === 'RUNTIME' && deployment?.id) {
            connectRuntimeWebSocket();
        }
        return () => {
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
            }
        };
    }, [logType, deployment?.id, connectRuntimeWebSocket]);

    // Clean up WebSocket on unmount
    useEffect(() => {
        return () => {
            wsRef.current?.close();
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
            }
        };
    }, []);

    // Poll build logs during active build (fallback when WS isn't available)
    useEffect(() => {
        if (logType !== 'BUILD' || !isBuilding || !deployment?.id) return;
        if (wsConnected) return;

        const fetchBuildLogs = async () => {
            try {
                const res = await fetch(`/api/v1/deployments/${deployment.id}/`, {
                    credentials: "include",
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.build_logs) {
                        // The previous implementation replaced the buffer
                        // every poll, which thrashed scroll position. We
                        // only push lines we haven't seen yet, keyed on a
                        // monotonically increasing position in the
                        // deployment's saved log. We can't be perfectly
                        // sure we haven't seen something before, so we
                        // re-seed the full buffer the first time and only
                        // re-seed when the size changes (i.e. new lines
                        // appeared since last poll). The viewer will
                        // dedupe by id.
                        runtimeViewerRef.current?.clear();
                        buildViewerRef.current?.clear();
                        const lines: LogLine[] = data.build_logs
                            .split('\n')
                            .filter((l: string) => l.length > 0)
                            .map((text: string) => ({ id: makeId(), text }));
                        buildViewerRef.current?.append(lines);
                    }
                }
            } catch {
                // Silently fail
            }
        };

        fetchBuildLogs();
        const interval = setInterval(fetchBuildLogs, 3000);
        return () => clearInterval(interval);
    }, [logType, isBuilding, deployment?.id, wsConnected, makeId]);

    // REST fallback for runtime logs
    useEffect(() => {
        if (logType !== 'RUNTIME' || !deployment?.id) return;
        if (wsConnected) return;

        let cancelled = false;
        let lastSize = 0;
        const fetchRuntimeLogs = async () => {
            try {
                const res = await fetch(`/api/v1/deployments/${deployment.id}/runtime-logs/?tail=200`, {
                    credentials: "include",
                });
                if (cancelled) return;
                if (res.ok) {
                    const data = await res.json();
                    const logs = data.runtime_logs || '';
                    if (logs.length !== lastSize) {
                        // New content — replace. Live WS is the
                        // preferred path; this fallback is only for
                        // the polling case where we cannot dedupe.
                        lastSize = logs.length;
                        runtimeViewerRef.current?.clear();
                        runtimeViewerRef.current?.appendRaw(logs);
                    }
                    setRuntimeMessage(data.message || '');
                } else {
                    setRuntimeMessage('Failed to fetch runtime logs.');
                }
            } catch {
                if (!cancelled) setRuntimeMessage('Could not connect to the API.');
            }
        };

        fetchRuntimeLogs();
        const interval = setInterval(fetchRuntimeLogs, 3000);
        return () => {
            cancelled = true;
            clearInterval(interval);
        };
    }, [logType, deployment?.id, wsConnected]);

    // ---- Copy visible logs (works through the viewer's filtered DOM) ----
    const copyVisibleLogs = useCallback(async () => {
        const viewer = logType === 'BUILD' ? buildViewerRef.current : runtimeViewerRef.current;
        // We can't get the *filtered* list out of the ref directly, so we
        // walk the visible <pre> elements. This is more accurate than
        // re-filtering the buffer because it matches exactly what the
        // user sees on screen.
        const container = document.querySelector('[data-log-scroller]') as HTMLElement | null;
        if (!container) {
            toast({ title: 'No logs to copy' });
            return;
        }
        const lines = Array.from(container.querySelectorAll('li pre'))
            .map((el) => el.textContent || '')
            .filter((t) => t.length > 0);
        if (lines.length === 0) {
            toast({ title: 'No logs to copy' });
            return;
        }
        try {
            await navigator.clipboard.writeText(lines.join('\n'));
            setCopied(true);
            toast({
                title: 'Logs copied',
                description: `Copied ${lines.length} visible line${lines.length === 1 ? '' : 's'}.`,
            });
            setTimeout(() => setCopied(false), 1200);
        } catch {
            toast({ title: 'Failed to copy logs', variant: 'destructive' });
        }
        // Suppress unused-var lint for viewer
        void viewer;
    }, [logType, toast]);

    const viewerHeight = 'h-[600px]';

    return (
        <div className="bg-[#09090b] border border-border rounded-xl overflow-hidden font-mono text-xs shadow-2xl">
            {/* Header / Controls */}
            <div className="bg-white/5 p-3 border-b border-white/10 flex flex-wrap justify-between items-center gap-2">
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
                <div className="text-zinc-500 font-sans text-xs flex items-center gap-2 flex-wrap">
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
                            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                            <span className="text-green-400 font-bold">LIVE</span>
                            {wsConnected && <Radio size={10} className="text-green-500" />}
                        </>
                    )}
                    {logType === 'BUILD' && !isBuilding && deployment?.build_logs && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-green-500" />
                            Build Logs
                        </>
                    )}
                    {logType === 'RUNTIME' && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
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
            <div className="p-3">
                {logType === 'BUILD' && (
                    <div className="space-y-3">
                        {pipelineStages.length > 0 && (
                            <div className="px-4">
                                <PipelineVisualizer stages={pipelineStages} />
                            </div>
                        )}

                        {deployment?.ai_diagnosis && (
                            <div className="bg-emerald-500/10 border-l-2 border-emerald-500 p-4 text-emerald-200 rounded-r-lg">
                                <strong className="flex items-center gap-2 mb-2 text-emerald-400 font-sans uppercase tracking-wider text-[10px]">
                                    <Zap size={12} /> AI Insight
                                </strong>
                                {deployment.ai_diagnosis}
                            </div>
                        )}

                        <div data-log-scroller>
                            <LiveLogViewer
                                ref={buildViewerRef}
                                className="bg-zinc-950"
                                heightClass={viewerHeight}
                                emptyMessage={isBuilding ? 'Waiting for build output…' : 'No build logs.'}
                                shortcutsHint="Space: pause · End: jump live · c: clear"
                            />
                        </div>

                        {isBuilding && (
                            <div className="flex items-center gap-2 text-yellow-500/80">
                                <RefreshCw size={12} className="animate-spin" />
                                <span className="text-xs font-sans">Build in progress… live updates enabled</span>
                            </div>
                        )}
                    </div>
                )}

                {logType === 'RUNTIME' && (
                    <div className="space-y-3">
                        {runtimeMessage && (
                            <div className="flex items-center gap-2 text-zinc-500 text-xs font-sans px-1">
                                <Terminal className="h-3.5 w-3.5" />
                                {runtimeMessage}
                            </div>
                        )}
                        <div data-log-scroller>
                            <LiveLogViewer
                                ref={runtimeViewerRef}
                                className="bg-zinc-950"
                                heightClass={viewerHeight}
                                emptyMessage="No runtime logs available."
                                shortcutsHint="Space: pause · End: jump live · c: clear"
                            />
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
