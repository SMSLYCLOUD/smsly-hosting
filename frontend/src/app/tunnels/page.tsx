'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Radio, Wifi, WifiOff, Plus, Trash2, RefreshCw, Copy, ExternalLink,
  ArrowUpDown, Globe2, Lock, Zap, Clock, BarChart3, Play, Users,
  Loader2, CheckCircle2, XCircle, Terminal, Hash
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { tunnelsApi, type Tunnel, type TunnelRequest, type ReservedSubdomain } from '@/lib/api';
import { useConfirm } from '@/components/ui/confirm-dialog';

export default function TunnelsPage() {
  const confirm = useConfirm();
  const [tunnels, setTunnels] = useState<Tunnel[]>([]);
  const [subdomains, setSubdomains] = useState<ReservedSubdomain[]>([]);
  const [subdomainLimit, setSubdomainLimit] = useState(0);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selectedTunnel, setSelectedTunnel] = useState<string | null>(null);
  const [requests, setRequests] = useState<TunnelRequest[]>([]);
  const [requestsLoading, setRequestsLoading] = useState(false);

  // Create form state
  const [showCreate, setShowCreate] = useState(false);
  const [newPort, setNewPort] = useState('3000');
  const [newSubdomain, setNewSubdomain] = useState('');

  // Subdomain form
  const [showSubdomainForm, setShowSubdomainForm] = useState(false);
  const [newReserve, setNewReserve] = useState('');

  const [copied, setCopied] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [tuns, subs] = await Promise.all([
        tunnelsApi.list(),
        tunnelsApi.subdomains().catch(() => ({ subdomains: [], limit: 0 })),
      ]);
      setTunnels(tuns);
      setSubdomains(subs.subdomains || []);
      setSubdomainLimit(subs.limit);
    } catch (err) {
      console.error('Failed to fetch tunnels:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleCreate = async () => {
    if (!newPort) return;
    setCreating(true);
    try {
      await tunnelsApi.create({
        local_port: parseInt(newPort),
        subdomain: newSubdomain || undefined,
      });
      setShowCreate(false);
      setNewPort('3000');
      setNewSubdomain('');
      fetchData();
    } catch (err: any) {
      alert(err.response?.data?.error || 'Failed to create tunnel');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!await confirm({ title: 'Close tunnel?', message: 'Close this tunnel? Active connections will be dropped.', variant: 'destructive', confirmText: 'Close' })) return;
    try {
      await tunnelsApi.delete(id);
      fetchData();
    } catch (err) {
      console.error('Delete failed:', err);
    }
  };

  const handleViewRequests = async (tunnelId: string) => {
    if (selectedTunnel === tunnelId) {
      setSelectedTunnel(null);
      return;
    }
    setSelectedTunnel(tunnelId);
    setRequestsLoading(true);
    try {
      const reqs = await tunnelsApi.requests(tunnelId);
      setRequests(reqs);
    } catch (err) {
      console.error('Failed to fetch requests:', err);
    } finally {
      setRequestsLoading(false);
    }
  };

  const handleReplay = async (tunnelId: string, requestId: string) => {
    try {
      await tunnelsApi.replay(tunnelId, requestId);
      // Refresh request list
      const reqs = await tunnelsApi.requests(tunnelId);
      setRequests(reqs);
    } catch (err) {
      console.error('Replay failed:', err);
    }
  };

  const handleReserveSubdomain = async () => {
    if (!newReserve) return;
    try {
      await tunnelsApi.reserveSubdomain(newReserve);
      setNewReserve('');
      setShowSubdomainForm(false);
      fetchData();
    } catch (err: any) {
      alert(err.response?.data?.error || 'Failed to reserve subdomain');
    }
  };

  const handleReleaseSubdomain = async (subdomain: string) => {
    if (!await confirm({ title: 'Release subdomain?', message: `Release ${subdomain}.tunnel.smsly.cloud? You may not be able to reclaim it.`, variant: 'destructive', confirmText: 'Release' })) return;
    try {
      await tunnelsApi.releaseSubdomain(subdomain);
      fetchData();
    } catch (err) {
      console.error('Release failed:', err);
    }
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const methodColor: Record<string, string> = {
    GET: 'text-emerald-500',
    POST: 'text-amber-500',
    PUT: 'text-blue-500',
    DELETE: 'text-red-500',
    PATCH: 'text-purple-500',
  };

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex-1 flex items-center justify-center p-8 relative z-10">
          <Loader2 className="h-8 w-8 animate-spin text-cyan-500" />
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="flex-1 p-8 relative z-10">
        <motion.div
          className="max-w-6xl mx-auto space-y-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/25">
                  <Radio className="text-white" size={22} />
                </div>
                Dev Tunnels
              </h1>
              <p className="text-muted-foreground mt-1">
                Expose local dev servers to the internet — like ngrok, but yours
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => fetchData()}
                className="px-3 py-2 rounded-lg border border-border text-sm flex items-center gap-2 hover:bg-muted/50 transition"
              >
                <RefreshCw size={14} />
              </button>
              <button
                onClick={() => setShowCreate(!showCreate)}
                className="px-4 py-2 rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 text-white text-sm font-semibold flex items-center gap-2 shadow-lg shadow-cyan-500/25"
              >
                <Plus size={14} />
                New Tunnel
              </button>
            </div>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { icon: <Wifi size={18} />, label: 'Active', value: tunnels.filter(t => t.is_active).length, color: 'text-emerald-500 bg-emerald-500/10' },
              { icon: <ArrowUpDown size={18} />, label: 'Total Requests', value: tunnels.reduce((s, t) => s + t.request_count, 0), color: 'text-blue-500 bg-blue-500/10' },
              { icon: <BarChart3 size={18} />, label: 'Bandwidth', value: formatBytes(tunnels.reduce((s, t) => s + (t.bandwidth_used || 0), 0)), color: 'text-amber-500 bg-amber-500/10' },
              { icon: <Globe2 size={18} />, label: 'Subdomains', value: `${subdomains.length}${subdomainLimit !== -1 ? `/${subdomainLimit}` : ''}`, color: 'text-purple-500 bg-purple-500/10' },
            ].map((stat, i) => (
              <motion.div
                key={stat.label}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                className="bg-card border border-border rounded-xl p-4"
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${stat.color}`}>{stat.icon}</div>
                  <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">{stat.label}</span>
                </div>
                <p className="text-lg font-bold">{stat.value}</p>
              </motion.div>
            ))}
          </div>

          {/* Create Form */}
          <AnimatePresence>
            {showCreate && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="bg-card border border-border rounded-xl p-5 space-y-4"
              >
                <h2 className="font-bold flex items-center gap-2">
                  <Terminal size={16} className="text-cyan-500" />
                  Create Tunnel
                </h2>
                <p className="text-xs text-muted-foreground">
                  Or use the CLI: <code className="bg-muted px-1.5 py-0.5 rounded text-[11px]">smsly-tunnel 3000 --subdomain myapp</code>
                </p>
                <div className="flex gap-3">
                  <div className="flex-1">
                    <label className="text-xs font-semibold text-muted-foreground mb-1 block">Local Port</label>
                    <input
                      value={newPort}
                      onChange={e => setNewPort(e.target.value)}
                      placeholder="3000"
                      type="number"
                      className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                  </div>
                  <div className="flex-1">
                    <label className="text-xs font-semibold text-muted-foreground mb-1 block">Subdomain (optional)</label>
                    <div className="flex items-center gap-1">
                      <input
                        value={newSubdomain}
                        onChange={e => setNewSubdomain(e.target.value)}
                        placeholder="myapp"
                        className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                      />
                      <span className="text-xs text-muted-foreground">.tunnel.smsly.cloud</span>
                    </div>
                  </div>
                  <div className="flex items-end">
                    <button
                      onClick={handleCreate}
                      disabled={creating}
                      className="px-4 py-2 rounded-lg bg-cyan-500 text-white text-sm font-semibold flex items-center gap-2 disabled:opacity-50"
                    >
                      {creating ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                      Create
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Tunnels List */}
          <div className="space-y-3">
            {tunnels.length === 0 ? (
              <div className="bg-card border border-border rounded-xl p-12 text-center">
                <Radio size={40} className="mx-auto text-muted-foreground/30 mb-4" />
                <h3 className="text-lg font-bold">No active tunnels</h3>
                <p className="text-sm text-muted-foreground mt-1 mb-4">
                  Create one from here or use the CLI
                </p>
                <code className="bg-muted px-3 py-1.5 rounded text-xs">
                  npx @smsly/tunnel 3000
                </code>
              </div>
            ) : (
              tunnels.map(tunnel => (
                <motion.div
                  key={tunnel.tunnel_id}
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-card border border-border rounded-xl overflow-hidden"
                >
                  <div className="p-4 flex items-center gap-4">
                    {/* Status */}
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                      tunnel.is_active ? 'bg-emerald-500/10' : 'bg-zinc-500/10'
                    }`}>
                      {tunnel.is_active
                        ? <Wifi size={18} className="text-emerald-500" />
                        : <WifiOff size={18} className="text-zinc-500" />
                      }
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-sm truncate">{tunnel.public_url}</span>
                        <button
                          onClick={() => copyToClipboard(tunnel.public_url, tunnel.tunnel_id)}
                          className="text-muted-foreground hover:text-foreground transition"
                        >
                          {copied === tunnel.tunnel_id ? <CheckCircle2 size={12} className="text-emerald-500" /> : <Copy size={12} />}
                        </button>
                        <a href={tunnel.public_url} target="_blank" className="text-muted-foreground hover:text-foreground transition">
                          <ExternalLink size={12} />
                        </a>
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1"><Hash size={10} /> :{tunnel.local_port}</span>
                        <span className="flex items-center gap-1"><ArrowUpDown size={10} /> {tunnel.request_count} reqs</span>
                        <span className="flex items-center gap-1"><BarChart3 size={10} /> {formatBytes(tunnel.bandwidth_used || 0)}</span>
                        <span className="flex items-center gap-1"><Clock size={10} /> {new Date(tunnel.created_at).toLocaleTimeString()}</span>
                        {(tunnel.shared_with || []).length > 0 && (
                          <span className="flex items-center gap-1"><Users size={10} /> {(tunnel.shared_with || []).length} shared</span>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => handleViewRequests(tunnel.tunnel_id)}
                        className={`px-3 py-1.5 rounded text-xs font-semibold transition ${
                          selectedTunnel === tunnel.tunnel_id
                            ? 'bg-cyan-500/10 text-cyan-500'
                            : 'hover:bg-muted/50 text-muted-foreground'
                        }`}
                      >
                        Requests
                      </button>
                      <button
                        onClick={() => handleDelete(tunnel.tunnel_id)}
                        className="p-1.5 rounded text-red-400 hover:bg-red-500/10 transition"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>

                  {/* Request Logs */}
                  <AnimatePresence>
                    {selectedTunnel === tunnel.tunnel_id && (
                      <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: 'auto' }}
                        exit={{ height: 0 }}
                        className="overflow-hidden border-t border-border"
                      >
                        <div className="p-4 bg-muted/20 max-h-64 overflow-y-auto">
                          {requestsLoading ? (
                            <div className="flex justify-center py-4">
                              <Loader2 size={16} className="animate-spin text-muted-foreground" />
                            </div>
                          ) : requests.length === 0 ? (
                            <p className="text-xs text-muted-foreground text-center py-4">No requests yet</p>
                          ) : (
                            <div className="space-y-1">
                              {requests.map(req => (
                                <div key={req.id} className="flex items-center gap-3 text-xs p-2 rounded hover:bg-muted/30 transition">
                                  <span className={`font-bold w-12 ${methodColor[req.method] || 'text-zinc-400'}`}>{req.method}</span>
                                  <span className="flex-1 truncate font-mono">{req.path}</span>
                                  <span className={`font-bold ${req.status < 400 ? 'text-emerald-500' : 'text-red-500'}`}>{req.status}</span>
                                  <span className="text-muted-foreground w-12 text-right">{req.duration}ms</span>
                                  <button
                                    onClick={() => handleReplay(tunnel.tunnel_id, req.id)}
                                    className="p-1 rounded hover:bg-cyan-500/10 text-muted-foreground hover:text-cyan-500 transition"
                                    title="Replay request"
                                  >
                                    <Play size={10} />
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              ))
            )}
          </div>

          {/* Subdomains Section */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-bold text-lg flex items-center gap-2">
                <Globe2 size={18} className="text-purple-500" />
                Reserved Subdomains
                <span className="text-xs text-muted-foreground font-normal">— Pro/Team only</span>
              </h2>
              <button
                onClick={() => setShowSubdomainForm(!showSubdomainForm)}
                className="text-xs px-3 py-1.5 rounded-lg bg-purple-500/10 text-purple-500 font-semibold hover:bg-purple-500/20 transition flex items-center gap-1"
              >
                <Plus size={12} /> Reserve
              </button>
            </div>

            <AnimatePresence>
              {showSubdomainForm && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="flex gap-2"
                >
                  <input
                    value={newReserve}
                    onChange={e => setNewReserve(e.target.value)}
                    placeholder="myapp"
                    className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                  />
                  <span className="flex items-center text-xs text-muted-foreground">.tunnel.smsly.cloud</span>
                  <button
                    onClick={handleReserveSubdomain}
                    className="px-3 py-2 rounded-lg bg-purple-500 text-white text-xs font-semibold"
                  >
                    Reserve
                  </button>
                </motion.div>
              )}
            </AnimatePresence>

            {subdomains.length === 0 ? (
              <p className="text-xs text-muted-foreground">No reserved subdomains. Reserve one to always get the same URL.</p>
            ) : (
              <div className="space-y-2">
                {subdomains.map(sub => (
                  <div key={sub.subdomain} className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/50">
                    <Lock size={14} className="text-purple-500" />
                    <span className="font-semibold text-sm flex-1">
                      {sub.subdomain}<span className="text-muted-foreground font-normal">.tunnel.smsly.cloud</span>
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      {new Date(sub.created_at).toLocaleDateString()}
                    </span>
                    <button
                      onClick={() => handleReleaseSubdomain(sub.subdomain)}
                      className="p-1.5 rounded text-red-400 hover:bg-red-500/10 transition"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* CLI Instructions */}
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="font-bold text-lg flex items-center gap-2 mb-3">
              <Terminal size={18} className="text-zinc-400" />
              CLI Usage
            </h2>
            <div className="grid grid-cols-2 gap-3">
              {[
                { cmd: 'npx @smsly/tunnel 3000', desc: 'Tunnel port 3000' },
                { cmd: 'smsly-tunnel 3000 --subdomain myapp', desc: 'Custom subdomain' },
                { cmd: 'smsly-tunnel 3000 --inspect', desc: 'With request inspector' },
                { cmd: 'smsly-tunnel login <token>', desc: 'Authenticate for Pro features' },
              ].map(item => (
                <div key={item.cmd} className="flex items-start gap-2 p-2 rounded bg-muted/30">
                  <code className="text-[11px] font-mono flex-1 text-cyan-400">{item.cmd}</code>
                  <span className="text-[10px] text-muted-foreground whitespace-nowrap">{item.desc}</span>
                </div>
              ))}
            </div>
          </div>
        </motion.div>
      </div>
    </DashboardShell>
  );
}
