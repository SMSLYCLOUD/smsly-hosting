/**
 * SMSLY Tunnel Dashboard Components
 * 
 * React components for the SMSLY Hosting dashboard to display
 * and manage active tunnels.
 */

'use client';

import React, { useState, useEffect } from 'react';

interface Tunnel {
    tunnelId: string;
    subdomain: string;
    publicUrl: string;
    localPort: number;
    createdAt: string;
    requestCount: number;
    isActive: boolean;
}

interface TunnelRequest {
    id: string;
    method: string;
    path: string;
    status: number | null;
    responseTimeMs: number | null;
    timestamp: string;
}

export function TunnelDashboard() {
    const [tunnels, setTunnels] = useState<Tunnel[]>([]);
    const [selectedTunnel, setSelectedTunnel] = useState<string | null>(null);
    const [requests, setRequests] = useState<TunnelRequest[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchTunnels();
        const interval = setInterval(fetchTunnels, 5000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        if (selectedTunnel) {
            fetchRequests(selectedTunnel);
        }
    }, [selectedTunnel]);

    async function fetchTunnels() {
        try {
            const res = await fetch('/api/v1/tunnels/');
            const data = await res.json();
            setTunnels(data.tunnels || []);
            setLoading(false);
        } catch (err) {
            console.error('Failed to fetch tunnels:', err);
            setLoading(false);
        }
    }

    async function fetchRequests(tunnelId: string) {
        try {
            const res = await fetch(`/api/v1/tunnels/${tunnelId}/requests/`);
            const data = await res.json();
            setRequests(data.requests || []);
        } catch (err) {
            console.error('Failed to fetch requests:', err);
        }
    }

    async function replayRequest(tunnelId: string, requestId: string) {
        try {
            await fetch(`/api/v1/tunnels/${tunnelId}/replay/${requestId}/`, {
                method: 'POST',
            });
            fetchRequests(tunnelId);
        } catch (err) {
            console.error('Failed to replay request:', err);
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-white font-bold text-lg">
                        T
                    </div>
                    <div>
                        <h2 className="text-xl font-semibold text-white">Development Tunnels</h2>
                        <p className="text-sm text-gray-400">Expose local servers to the internet</p>
                    </div>
                </div>
                <div className="flex items-center gap-2 text-sm text-gray-400">
                    <span className={`w-2 h-2 rounded-full ${tunnels.length > 0 ? 'bg-green-500 animate-pulse' : 'bg-gray-500'}`}></span>
                    {tunnels.length} active tunnel{tunnels.length !== 1 ? 's' : ''}
                </div>
            </div>

            {/* Tunnels List */}
            {tunnels.length === 0 ? (
                <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-8 text-center">
                    <div className="text-4xl mb-4">🔗</div>
                    <h3 className="text-lg font-medium text-white mb-2">No Active Tunnels</h3>
                    <p className="text-gray-400 mb-4">Start a tunnel to expose your local server</p>
                    <div className="bg-gray-800/50 rounded-lg p-4 font-mono text-sm text-gray-300">
                        <code>npx @smsly/tunnel 3000</code>
                    </div>
                </div>
            ) : (
                <div className="grid gap-4">
                    {tunnels.map((tunnel) => (
                        <div
                            key={tunnel.tunnelId}
                            className={`bg-gray-900/50 border rounded-xl p-4 cursor-pointer transition-all hover:border-blue-500/50 ${selectedTunnel === tunnel.tunnelId ? 'border-blue-500' : 'border-gray-800'
                                }`}
                            onClick={() => setSelectedTunnel(tunnel.tunnelId)}
                        >
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                                        <span className="font-mono text-cyan-400">{tunnel.publicUrl}</span>
                                    </div>
                                    <div className="text-sm text-gray-400 mt-1">
                                        → localhost:{tunnel.localPort}
                                    </div>
                                </div>
                                <div className="text-right text-sm text-gray-400">
                                    <div>{tunnel.requestCount} requests</div>
                                    <div>{new Date(tunnel.createdAt).toLocaleTimeString()}</div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Request Inspector */}
            {selectedTunnel && (
                <div className="bg-gray-900/50 border border-gray-800 rounded-xl">
                    <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
                        <h3 className="font-medium text-white">Request Inspector</h3>
                        <button
                            onClick={() => setSelectedTunnel(null)}
                            className="text-gray-400 hover:text-white"
                        >
                            ✕
                        </button>
                    </div>
                    <div className="divide-y divide-gray-800 max-h-96 overflow-y-auto">
                        {requests.length === 0 ? (
                            <div className="p-8 text-center text-gray-400">
                                <div className="text-2xl mb-2">📡</div>
                                Waiting for requests...
                            </div>
                        ) : (
                            requests.map((req) => (
                                <div key={req.id} className="p-3 hover:bg-gray-800/50">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-2">
                                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${req.method === 'GET' ? 'bg-green-900/50 text-green-400' :
                                                    req.method === 'POST' ? 'bg-yellow-900/50 text-yellow-400' :
                                                        req.method === 'PUT' ? 'bg-blue-900/50 text-blue-400' :
                                                            req.method === 'DELETE' ? 'bg-red-900/50 text-red-400' :
                                                                'bg-gray-700 text-gray-300'
                                                }`}>
                                                {req.method}
                                            </span>
                                            <span className="font-mono text-sm text-gray-300">{req.path}</span>
                                            {req.status && (
                                                <span className={`text-xs ${req.status < 400 ? 'text-green-400' :
                                                        req.status < 500 ? 'text-yellow-400' : 'text-red-400'
                                                    }`}>
                                                    {req.status}
                                                </span>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <span className="text-xs text-gray-500">
                                                {req.responseTimeMs ? `${req.responseTimeMs}ms` : 'pending'}
                                            </span>
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    replayRequest(selectedTunnel, req.id);
                                                }}
                                                className="text-xs text-blue-400 hover:text-blue-300"
                                            >
                                                ⟳ Replay
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            )}

            {/* Install Instructions */}
            <div className="bg-gray-900/30 border border-gray-800 rounded-xl p-4">
                <h4 className="text-sm font-medium text-gray-300 mb-2">Quick Start</h4>
                <div className="space-y-2 text-sm">
                    <div className="bg-gray-800/50 rounded p-2 font-mono text-gray-300">
                        npm install -g @smsly/tunnel
                    </div>
                    <div className="bg-gray-800/50 rounded p-2 font-mono text-gray-300">
                        smsly-tunnel 3000 --inspect
                    </div>
                </div>
            </div>
        </div>
    );
}
