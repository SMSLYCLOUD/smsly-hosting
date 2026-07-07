'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { RefreshCw, Copy, Check, Terminal } from 'lucide-react';
import { addonsApi } from '@/lib/api';
import { getWsUrl } from '@/lib/websocket';
import { useToast } from '@/components/ui/use-toast';

type LogFilterType = 'ALL' | 'APP' | 'SYSTEM' | 'WARNING' | 'ERROR';

function classifyLogLine(line: string): Exclude<LogFilterType, 'ALL'> {
    const lower = line.toLowerCase();
    if (/\berror\b|\bfatal\b|\bpanic\b|\btraceback\b|\bexception\b/.test(lower)) return 'ERROR';
    if (/\bwarn\b|\bdeprecated\b|\bslow\b|\bretry\b/.test(lower)) return 'WARNING';
    if (/\bsystemd\b|\bkernel\b|\bdocker\b|\bcontainerd\b|\bnginx\b|\bpostgres\b|\bredis\b/.test(lower)) return 'SYSTEM';
    return 'APP';
}

function matchesFilter(line: string, filter: LogFilterType): boolean {
    if (filter === 'ALL') return true;
    return classifyLogLine(line) === filter;
}

interface AddonLogsViewerProps {
    addonId: string;
    addonType: string;
    status: string;
    compact?: boolean;
}

export function AddonLogsViewer({ addonId, addonType, status, compact = false }: AddonLogsViewerProps) {
    const { toast } = useToast();
    const [logs, setLogs] = useState('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [filter, setFilter] = useState<LogFilterType>('ALL');
    const [copied, setCopied] = useState(false);
    const [isLive, setIsLive] = useState(false);
    const logsEndRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

    const isActive = status === 'ACTIVE' || status === 'RUNNING';

    // Auto-scroll
    useEffect(() => {
        if (!compact) {
            logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
    }, [logs, compact]);

    // Fetch logs via REST API
    const fetchLogs = useCallback(async () => {
        try {
            const data = await addonsApi.getLogs(addonId, compact ? 100 : 200);
            setLogs(data.logs || '');
            setError(data.message || '');
        } catch {
            setError('Failed to fetch addon logs.');
        } finally {
            setLoading(false);
        }
    }, [addonId, compact]);

    // WebSocket for live streaming
    const connectWebSocket = useCallback(() => {
        if (!addonId || wsRef.current?.readyState === WebSocket.OPEN) return;

        const wsUrl = getWsUrl(`/ws/addon-logs/${addonId}/`);
        try {
            const ws = new WebSocket(wsUrl);
            ws.onopen = () => {
                setIsLive(true);
                setLoading(false);
            };
            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'initial_state') {
                        if (data.logs) setLogs(data.logs);
                    } else if (data.type === 'log') {
                        setLogs(prev => prev + '\n' + (data.log || ''));
                    } else if (data.error) {
                        setError(data.error);
                    }
                } catch {
                    // Non-JSON message
                }
            };
            ws.onclose = () => {
                setIsLive(false);
                if (isActive) {
                    reconnectTimer.current = setTimeout(connectWebSocket, 3000);
                }
            };
            ws.onerror = () => ws.close();
            wsRef.current = ws;
        } catch {
            // WebSocket not supported
        }
    }, [addonId, isActive]);

    // Connect: try WebSocket first, fall back to REST polling
    useEffect(() => {
        if (isActive) {
            connectWebSocket();
        } else {
            fetchLogs();
        }
        return () => {
            wsRef.current?.close();
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
        };
    }, [isActive, connectWebSocket, fetchLogs]);

    // REST polling fallback when WS not connected
    useEffect(() => {
        if (!isActive || isLive) return;
        fetchLogs();
        const interval = setInterval(fetchLogs, 5000);
        return () => clearInterval(interval);
    }, [isActive, isLive, fetchLogs]);

    // Non-active addons: just fetch once
    useEffect(() => {
        if (!isActive && loading) fetchLogs();
    }, [isActive, loading, fetchLogs]);

    const lines = logs ? logs.split('\n') : [];
    const filteredLines = lines.filter(l => matchesFilter(l, filter));

    const copyLogs = async () => {
        const content = filteredLines.join('\n');
        if (!content) {
            toast({ title: 'No logs to copy' });
            return;
        }
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
            toast({ title: 'Logs copied', description: `Copied ${filteredLines.length} lines.` });
            setTimeout(() => setCopied(false), 1200);
        } catch {
            toast({ title: 'Failed to copy', variant: 'destructive' });
        }
    };

    if (loading) {
        return (
            <div className={`flex items-center justify-center text-muted-foreground gap-2 ${compact ? 'h-32' : 'h-64'}`}>
                <RefreshCw className="w-4 h-4 animate-spin" /> Loading logs...
            </div>
        );
    }

    if (error && !logs) {
        return (
            <div className={`flex flex-col items-center justify-center text-center gap-3 ${compact ? 'h-32' : 'h-64'}`}>
                <Terminal className={`${compact ? 'h-6 w-6' : 'h-8 w-8'} text-zinc-600`} />
                <p className="text-zinc-500 font-sans text-sm">{error}</p>
            </div>
        );
    }

    return (
        <div className={`bg-[#09090b] border border-border rounded-xl overflow-hidden font-mono text-xs ${compact ? 'h-[300px]' : 'h-[500px]'} flex flex-col`}>
            {/* Header */}
            <div className="bg-white/5 p-3 border-b border-white/10 flex justify-between items-center">
                <div className="flex gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500/80" />
                    <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                    <div className="w-3 h-3 rounded-full bg-green-500/80" />
                </div>
                <div className="text-zinc-500 font-sans text-xs flex items-center gap-2">
                    <select
                        value={filter}
                        onChange={(e) => setFilter(e.target.value as LogFilterType)}
                        className="bg-black/40 border border-white/10 rounded px-2 py-1 text-[11px] text-zinc-300"
                    >
                        <option value="ALL">All</option>
                        <option value="APP">App</option>
                        <option value="SYSTEM">System</option>
                        <option value="WARNING">Warning</option>
                        <option value="ERROR">Error</option>
                    </select>
                    <button
                        onClick={copyLogs}
                        className="inline-flex items-center gap-1 rounded border border-white/10 bg-black/40 px-2 py-1 text-[11px] text-zinc-300 hover:bg-black/60"
                        title="Copy logs"
                    >
                        {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
                        Copy
                    </button>
                    {isLive && (
                        <>
                            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                            <span className="text-green-400 font-bold">LIVE</span>
                        </>
                    )}
                    {!isLive && isActive && (
                        <RefreshCw size={12} className="animate-spin text-blue-400" />
                    )}
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 p-4 overflow-y-auto text-zinc-300 leading-relaxed custom-scrollbar">
                {filteredLines.length > 0 ? (
                    <div className="whitespace-pre-wrap font-mono">
                        {filteredLines.map((line, i) => (
                            <div key={i} className="hover:bg-white/[0.02] py-px">
                                {line}
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className="flex flex-col items-center justify-center h-full text-center gap-3">
                        <Terminal className="h-8 w-8 text-zinc-600" />
                        <p className="text-zinc-500 font-sans text-sm">
                            {logs ? 'No logs match the selected filter.' : 'No logs available.'}
                        </p>
                    </div>
                )}
                <div ref={logsEndRef} />
            </div>
        </div>
    );
}
