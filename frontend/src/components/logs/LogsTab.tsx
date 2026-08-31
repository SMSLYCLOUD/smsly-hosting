import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Terminal, Zap, Clock, RefreshCw, Radio, Copy, Check } from 'lucide-react';
import { Deployment, getDeployment } from '@/lib/api';
import { getWsUrl } from '@/lib/websocket';
import { PipelineVisualizer, PipelineStage } from '@/components/deployments/PipelineVisualizer';
import { useToast } from '@/components/ui/use-toast';
import {
    LiveLogViewer,
    LiveLogViewerHandle,
    LogLine,
} from '@/components/logs/LiveLogViewer';

export function LogsTab({ deployment }: { deployment: Deployment | null }) {
    const { toast } = useToast();
    const [logType, setLogType] = useState<'BUILD' | 'RUNTIME'>('BUILD');
    const [runtimeMessage, setRuntimeMessage] = useState('');
    const [pipelineStages, setPipelineStages] = useState<PipelineStage[]>([]);
    const [wsConnected, setWsConnected] = useState(false);

    const buildViewerRef = useRef<LiveLogViewerHandle>(null);
    const runtimeViewerRef = useRef<LiveLogViewerHandle>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);
    const seqRef = useRef(0);
    const initialLoadDoneRef = useRef<{ build: boolean; runtime: boolean }>({ build: false, runtime: false });
    const activeWsTypeRef = useRef<'BUILD' | 'RUNTIME' | null>(null);

    const makeId = useCallback((prefix: string) => {
        seqRef.current += 1;
        return `${prefix}-${Date.now().toString(36)}-${seqRef.current.toString(36)}`;
    }, []);

    // Initial load of pipeline stages from prop
    useEffect(() => {
        if (deployment?.pipeline_stages) {
            setPipelineStages(deployment.pipeline_stages);
        }
    }, [deployment?.pipeline_stages]);

    const isBuilding = deployment?.status === 'BUILDING'
        || deployment?.status === 'QUEUED'
        || deployment?.status === 'PENDING';

    // ---- REST fallback: load build_logs from the deployment object ----
    // The deployment prop is refreshed every 3s by the parent, but on first
    // mount it may be null. Fetch the deployment once explicitly so the
    // build tab never shows empty just because the WS hasn't connected yet.
    useEffect(() => {
        if (!deployment?.id) return;
        if (logType !== 'BUILD') return;
        if (initialLoadDoneRef.current.build) return;
        const logs = deployment.build_logs || '';
        if (logs) {
            const lines: LogLine[] = logs
                .split('\n')
                .filter((l) => l.length > 0)
                .map((text) => ({
                    // Use content-derived id so a subsequent WS initial_state
                    // (which may send the same lines again) dedupes naturally.
                    id: `bld-${text.length}-${text.slice(0, 80).replace(/\s+/g, '_')}`,
                    text,
                }));
            buildViewerRef.current?.clear();
            buildViewerRef.current?.append(lines);
            initialLoadDoneRef.current.build = true;
        } else {
            // Even if empty, mark as done so we don't re-seed on every parent
            // re-render. WS will populate it on connect.
            initialLoadDoneRef.current.build = true;
        }
    }, [logType, deployment?.id, deployment?.build_logs]);

    // Same for runtime logs on first open
    useEffect(() => {
        if (!deployment?.id) return;
        if (logType !== 'RUNTIME') return;
        if (initialLoadDoneRef.current.runtime) return;
        // For runtime logs we can't preload from the deployment prop — the
        // WS is the source of truth (it gives us live or crash logs).
        // Mark as done so we don't re-init on every parent re-render.
        initialLoadDoneRef.current.runtime = true;
    }, [logType, deployment?.id]);

    // ---- WebSocket: build logs ----
    const connectBuildWebSocket = useCallback(() => {
        if (!deployment?.id) return;
        if (activeWsTypeRef.current === 'BUILD' && wsRef.current?.readyState === WebSocket.OPEN) return;
        // Close any previous WS so we don't have two open
        if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
            try { wsRef.current.close(); } catch { /* ignore */ }
        }

        const wsUrl = getWsUrl(`/ws/build-logs/${deployment.id}/`);

        try {
            const ws = new WebSocket(wsUrl);
            activeWsTypeRef.current = 'BUILD';

            ws.onopen = () => {
                setWsConnected(true);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'initial_state') {
                        if (data.build_logs) {
                            // MERGE: dedupe by line-content id so the
                            // REST-seeded lines don't get re-painted, and
                            // the buffer isn't wiped on every reconnect.
                            const lines: LogLine[] = data.build_logs
                                .split('\n')
                                .filter((l: string) => l.length > 0)
                                .map((text: string) => ({
                                    id: `bld-${text.length}-${text.slice(0, 80).replace(/\s+/g, '_')}`,
                                    text,
                                }));
                            buildViewerRef.current?.merge(lines);
                        }
                        if (data.stages) {
                            setPipelineStages(data.stages);
                        }
                    } else if (data.type === 'build_log') {
                        if (data.log) {
                            // Live log lines get a unique runtime id.
                            buildViewerRef.current?.appendRaw(data.log, undefined, 'live');
                        }
                    } else if (data.type === 'pipeline_update') {
                        if (data.stages) setPipelineStages(data.stages);
                    }
                } catch {
                    // Non-JSON message — ignore
                }
            };

            ws.onclose = () => {
                setWsConnected(false);
                activeWsTypeRef.current = null;
                // Reconnect only if the build tab is still active AND we
                // don't already have a fresh fetch. We always reconnect
                // (even when the build is finished) so the user can come
                // back to the tab and see the full build log.
                if (logType === 'BUILD') {
                    reconnectTimer.current = setTimeout(connectBuildWebSocket, 3000);
                }
            };

            ws.onerror = () => {
                try { ws.close(); } catch { /* ignore */ }
            };

            wsRef.current = ws;
        } catch {
            // WebSocket not supported or connection failed
        }
    }, [deployment?.id, logType]);

    // ---- WebSocket: runtime logs ----
    const connectRuntimeWebSocket = useCallback(() => {
        if (!deployment?.id) return;
        if (activeWsTypeRef.current === 'RUNTIME' && wsRef.current?.readyState === WebSocket.OPEN) return;
        if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
            try { wsRef.current.close(); } catch { /* ignore */ }
        }

        const wsUrl = getWsUrl(`/ws/runtime-logs/${deployment.id}/`);

        try {
            const ws = new WebSocket(wsUrl);
            activeWsTypeRef.current = 'RUNTIME';

            ws.onopen = () => {
                setWsConnected(true);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'initial_state') {
                        // MERGE: don't wipe existing lines.
                        if (data.logs) {
                            runtimeViewerRef.current?.mergeRaw(data.logs, undefined, 'rt');
                        }
                        setRuntimeMessage(data.message || '');
                        if (data.container_status && data.container_status !== 'running') {
                            setRuntimeMessage(
                                data.source === 'build_logs'
                                    ? 'Container is not running. Showing saved crash logs from build.'
                                    : `Container status: ${data.container_status}`
                            );
                        }
                    } else if (data.type === 'log') {
                        if (data.log) {
                            runtimeViewerRef.current?.appendRaw(data.log, undefined, 'rt-live');
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
                activeWsTypeRef.current = null;
                if (logType === 'RUNTIME') {
                    reconnectTimer.current = setTimeout(connectRuntimeWebSocket, 5000);
                }
            };

            ws.onerror = () => {
                try { ws.close(); } catch { /* ignore */ }
            };

            wsRef.current = ws;
        } catch {
            // WebSocket not supported
        }
    }, [deployment?.id, logType]);

    // Connect WS for the active tab. NOTE: we no longer gate on isBuilding
    // — the build tab needs the WS even for finished/failed deployments
    // so it can show the persisted build_logs.
    useEffect(() => {
        if (!deployment?.id) return;
        if (logType === 'BUILD') {
            connectBuildWebSocket();
        } else if (logType === 'RUNTIME') {
            connectRuntimeWebSocket();
        }
        return () => {
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
                reconnectTimer.current = null;
            }
        };
    }, [logType, deployment?.id, connectBuildWebSocket, connectRuntimeWebSocket]);

    // Clean up WS on unmount
    useEffect(() => {
        return () => {
            if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
                try { wsRef.current.close(); } catch { /* ignore */ }
            }
            if (reconnectTimer.current) {
                clearTimeout(reconnectTimer.current);
                reconnectTimer.current = null;
            }
        };
    }, []);

    // REST polling fallback: only used when WS is unavailable (the WS
    // endpoint itself didn't accept the connection). This does a merge
    // with line-content-hash ids so identical content is deduped.
    useEffect(() => {
        if (!deployment?.id) return;
        if (wsConnected) return; // WS is doing the work
        if (logType === 'BUILD') {
            let cancelled = false;
            const fetchBuildLogs = async () => {
                try {
                    const res = await fetch(`/api/v1/deployments/${deployment.id}/`, {
                        credentials: 'include',
                    });
                    if (cancelled) return;
                    if (res.ok) {
                        const data = await res.json();
                        if (data.build_logs) {
                            const lines: LogLine[] = data.build_logs
                                .split('\n')
                                .filter((l: string) => l.length > 0)
                                .map((text: string) => ({
                                    id: `bld-${text.length}-${text.slice(0, 80).replace(/\s+/g, '_')}`,
                                    text,
                                }));
                            buildViewerRef.current?.merge(lines);
                        }
                    }
                } catch {
                    // Silently fail
                }
            };
            fetchBuildLogs();
            const interval = setInterval(fetchBuildLogs, 3000);
            return () => { cancelled = true; clearInterval(interval); };
        }
        if (logType === 'RUNTIME') {
            let cancelled = false;
            const fetchRuntimeLogs = async () => {
                try {
                    const res = await fetch(`/api/v1/deployments/${deployment.id}/runtime-logs/?tail=200`, {
                        credentials: 'include',
                    });
                    if (cancelled) return;
                    if (res.ok) {
                        const data = await res.json();
                        const logs = data.runtime_logs || '';
                        if (logs) {
                            runtimeViewerRef.current?.mergeRaw(logs, undefined, 'rt-poll');
                        }
                        setRuntimeMessage(data.message || '');
                    }
                } catch {
                    if (!cancelled) setRuntimeMessage('Could not connect to the API.');
                }
            };
            fetchRuntimeLogs();
            const interval = setInterval(fetchRuntimeLogs, 3000);
            return () => { cancelled = true; clearInterval(interval); };
        }
        return;
    }, [logType, deployment?.id, wsConnected]);

    // ---- copy visible logs (walks the rendered <pre> elements) ----
    const copyVisibleLogs = useCallback(async () => {
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
            toast({
                title: 'Logs copied',
                description: `Copied ${lines.length} visible line${lines.length === 1 ? '' : 's'}.`,
            });
        } catch {
            toast({ title: 'Failed to copy logs', variant: 'destructive' });
        }
    }, [toast]);

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
                        <Copy size={12} /> Copy
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
                                emptyMessage={isBuilding ? 'Waiting for build output…' : 'No build logs recorded for this deployment.'}
                                shortcutsHint="Space: pause · End: jump to latest · c: clear"
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
                                emptyMessage="No runtime logs available. If the container is stopped, saved crash logs from the deployment will appear here when available."
                                shortcutsHint="Space: pause · End: jump to latest · c: clear"
                            />
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
