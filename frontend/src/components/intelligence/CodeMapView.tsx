'use client';

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Code2, FileCode, FolderTree, GitBranch, Loader2, Play, Search,
  Zap, Database, Globe, Box, Layers, ArrowRight, X, BarChart3,
  FileJson, Terminal, Braces, Hash
} from 'lucide-react';
import dynamic from 'next/dynamic';
import { codeAnalysisApi } from '@/lib/api';
import api from '@/lib/api';

const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

// ─── Types ──────────────────────────────────────────────────────────────────

interface CodeNode {
  id: string;
  type: string;  // file | directory | route | model (flexible for API compat)
  data: Record<string, any>;
}

interface CodeEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label?: string;
}

interface AnalysisResult {
  nodes: CodeNode[];
  edges: CodeEdge[];
  tech_stack: string[];
  stats: {
    files: number;
    directories: number;
    lines: number;
    languages: Record<string, number>;
  };
  summary: string;
}

interface ServiceOption {
  id: string;
  name: string;
  repository_url: string;
}

// ─── Node Config ────────────────────────────────────────────────────────────

const NODE_CONFIG: Record<string, { icon: string; baseColor: string; size: number }> = {
  directory: { icon: '📁', baseColor: '#6366f1', size: 6 },
  file:      { icon: '📄', baseColor: '#3b82f6', size: 4 },
  route:     { icon: '🌐', baseColor: '#10b981', size: 5 },
  model:     { icon: '🗄️', baseColor: '#f59e0b', size: 5 },
};

const EDGE_COLORS: Record<string, string> = {
  CONTAINS:      '#334155',
  IMPORT:        '#60a5fa',
  DEFINES_ROUTE: '#34d399',
  DEFINES_MODEL: '#fbbf24',
};

const LANG_ICONS: Record<string, React.ReactNode> = {
  python:     <FileCode className="text-[#3572A5]" size={12} />,
  typescript: <Braces className="text-[#3178c6]" size={12} />,
  javascript: <FileJson className="text-[#f1e05a]" size={12} />,
  go:         <Terminal className="text-[#00ADD8]" size={12} />,
  rust:       <Hash className="text-[#dea584]" size={12} />,
};

// ─── Component ──────────────────────────────────────────────────────────────

export default function CodeMapView() {
  const [services, setServices] = useState<ServiceOption[]>([]);
  const [selectedService, setSelectedService] = useState<string>('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'analyzing' | 'complete' | 'failed'>('idle');
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<CodeNode | null>(null);
  const [filter, setFilter] = useState<string>('all');
  const graphRef = useRef<any>(null);

  // Fetch services
  useEffect(() => {
    api.get('/services/', { params: { page_size: 100 } })
      .then(r => {
        const svcList = (r.data?.results || r.data || [])
          .filter((s: any) => s.repository_url)
          .map((s: any) => ({ id: s.id, name: s.name, repository_url: s.repository_url }));
        setServices(svcList);
      })
      .catch(() => {});
  }, []);

  // Poll for results
  useEffect(() => {
    if (!taskId || status !== 'analyzing') return;
    let active = true;
    const poll = async () => {
      while (active) {
        try {
          const res = await codeAnalysisApi.getResult(taskId);
          if (!active) break;
          if (res.status === 'complete' && res.data) {
            setResult(res.data);
            setStatus('complete');
            break;
          } else if (res.status === 'failed') {
            setError(res.error || 'Analysis failed');
            setStatus('failed');
            break;
          }
        } catch { break; }
        await new Promise(r => setTimeout(r, 3000));
      }
    };
    poll();
    return () => { active = false; };
  }, [taskId, status]);

  const startAnalysis = async () => {
    if (!selectedService) return;
    setStatus('analyzing');
    setError(null);
    setResult(null);
    setSelectedNode(null);
    try {
      const res = await codeAnalysisApi.analyze(selectedService);
      setTaskId(res.task_id);
    } catch (err: any) {
      setError(err.message || 'Failed to start analysis');
      setStatus('failed');
    }
  };

  // Graph data
  const graphData = useMemo(() => {
    if (!result) return { nodes: [], links: [] };

    const filteredNodes = filter === 'all'
      ? result.nodes
      : result.nodes.filter(n => n.type === filter);

    const nodeIds = new Set(filteredNodes.map(n => n.id));

    const links = result.edges
      .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map(e => ({
        source: e.source,
        target: e.target,
        color: EDGE_COLORS[e.type] || '#334155',
        type: e.type,
        label: e.label,
      }));

    const nodes = filteredNodes.map(n => {
      const config = NODE_CONFIG[n.type] || NODE_CONFIG.file;
      const fileColor = n.data.color || config.baseColor;
      const size = n.type === 'file'
        ? Math.max(2, Math.min(10, Math.sqrt(n.data.lines || 100) / 3))
        : config.size;

      return {
        id: n.id,
        name: n.data.label || n.data.name,
        type: n.type,
        color: fileColor,
        val: size,
        data: n.data,
        _node: n,
      };
    });

    return { nodes, links };
  }, [result, filter]);

  const handleNodeClick = useCallback((node: any) => {
    if (node._node) {
      setSelectedNode(node._node);
    }
    if (graphRef.current) {
      const distance = 120;
      const distRatio = 1 + distance / Math.hypot(node.x || 0, node.y || 0, node.z || 0);
      graphRef.current.cameraPosition(
        { x: (node.x || 0) * distRatio, y: (node.y || 0) * distRatio, z: (node.z || 0) * distRatio },
        node,
        1000,
      );
    }
  }, []);

  // ─── Render ───────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Service Selector + Analyze Button */}
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <select
            value={selectedService}
            onChange={e => setSelectedService(e.target.value)}
            className="w-full px-4 py-2.5 rounded-lg bg-background border border-border text-sm"
          >
            <option value="">Select a service to analyze...</option>
            {services.map(s => (
              <option key={s.id} value={s.id}>{s.name} — {s.repository_url}</option>
            ))}
          </select>
        </div>
        <button
          onClick={startAnalysis}
          disabled={!selectedService || status === 'analyzing'}
          className="px-6 py-2.5 rounded-lg bg-gradient-to-r from-purple-500 to-pink-600 text-white text-sm font-semibold flex items-center gap-2 disabled:opacity-50 shadow-lg shadow-purple-500/25"
        >
          {status === 'analyzing' ? (
            <><Loader2 size={14} className="animate-spin" /> Analyzing...</>
          ) : (
            <><Zap size={14} /> Analyze Codebase</>
          )}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Analyzing Spinner */}
      {status === 'analyzing' && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center justify-center py-20"
        >
          <div className="relative">
            <div className="w-20 h-20 rounded-full border-4 border-purple-500/20 border-t-purple-500 animate-spin" />
            <Code2 className="absolute inset-0 m-auto text-purple-500" size={28} />
          </div>
          <p className="text-muted-foreground mt-6 text-sm">
            Cloning repository, analyzing files, extracting imports...
          </p>
          <p className="text-muted-foreground/60 text-xs mt-1">
            This may take 30-60 seconds for large codebases
          </p>
        </motion.div>
      )}

      {/* Results */}
      {status === 'complete' && result && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
          {/* Stats Bar */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="bg-card border border-border rounded-xl p-3 text-center">
              <p className="text-lg font-bold">{result.stats.files}</p>
              <p className="text-[10px] text-muted-foreground uppercase">Files</p>
            </div>
            <div className="bg-card border border-border rounded-xl p-3 text-center">
              <p className="text-lg font-bold">{result.stats.directories}</p>
              <p className="text-[10px] text-muted-foreground uppercase">Directories</p>
            </div>
            <div className="bg-card border border-border rounded-xl p-3 text-center">
              <p className="text-lg font-bold">{result.stats.lines.toLocaleString()}</p>
              <p className="text-[10px] text-muted-foreground uppercase">Lines</p>
            </div>
            <div className="bg-card border border-border rounded-xl p-3 text-center">
              <p className="text-lg font-bold">
                {result.nodes.filter(n => n.type === 'route').length}
              </p>
              <p className="text-[10px] text-muted-foreground uppercase">Routes</p>
            </div>
            <div className="bg-card border border-border rounded-xl p-3 text-center">
              <p className="text-lg font-bold">
                {result.nodes.filter(n => n.type === 'model').length}
              </p>
              <p className="text-[10px] text-muted-foreground uppercase">Models</p>
            </div>
          </div>

          {/* Tech Stack + Filter */}
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted-foreground font-semibold uppercase">Stack:</span>
              {result.tech_stack.map(tech => (
                <span key={tech} className="px-2 py-0.5 text-xs rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/20">
                  {tech}
                </span>
              ))}
            </div>
            <div className="flex items-center gap-1 p-1 bg-muted/50 rounded-lg">
              {[
                { key: 'all', label: 'All', icon: <Layers size={12} /> },
                { key: 'file', label: 'Files', icon: <FileCode size={12} /> },
                { key: 'route', label: 'Routes', icon: <Globe size={12} /> },
                { key: 'model', label: 'Models', icon: <Database size={12} /> },
              ].map(f => (
                <button
                  key={f.key}
                  onClick={() => setFilter(f.key)}
                  className={`px-3 py-1 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                    filter === f.key
                      ? 'bg-purple-500/20 text-purple-400 shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {f.icon} {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* 3D Graph + Side Panel */}
          <div className="flex gap-4" style={{ height: 600 }}>
            {/* Graph */}
            <div className="flex-1 relative bg-zinc-950 rounded-xl border border-border overflow-hidden">
              <ForceGraph3D
                ref={graphRef}
                graphData={graphData}
                nodeLabel={(node: any) => node.name}
                nodeColor={(node: any) => node.color}
                nodeVal={(node: any) => node.val}
                linkColor={(link: any) => link.color}
                linkWidth={0.5}
                linkOpacity={0.4}
                linkDirectionalParticles={1}
                linkDirectionalParticleWidth={1}
                onNodeClick={handleNodeClick}
                backgroundColor="#09090b"
                showNavInfo={false}
                nodeThreeObjectExtend={true}
              />

              {/* Legend */}
              <div className="absolute bottom-4 left-4 bg-zinc-900/90 backdrop-blur-sm rounded-lg p-3 border border-zinc-800 text-xs space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#6366f1]" /> Directory
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#3b82f6]" /> File
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#10b981]" /> Route
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#f59e0b]" /> Model
                </div>
              </div>
            </div>

            {/* Side Panel */}
            <AnimatePresence>
              {selectedNode && (
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  className="w-80 bg-card border border-border rounded-xl overflow-hidden flex flex-col"
                >
                  <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                    <h3 className="font-bold text-sm flex items-center gap-2">
                      {selectedNode.type === 'file' && <FileCode size={14} className="text-blue-500" />}
                      {selectedNode.type === 'directory' && <FolderTree size={14} className="text-indigo-500" />}
                      {selectedNode.type === 'route' && <Globe size={14} className="text-emerald-500" />}
                      {selectedNode.type === 'model' && <Database size={14} className="text-amber-500" />}
                      {selectedNode.data.name}
                    </h3>
                    <button onClick={() => setSelectedNode(null)} className="text-muted-foreground hover:text-foreground">
                      <X size={14} />
                    </button>
                  </div>
                  <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
                    {/* Path */}
                    {selectedNode.data.path && (
                      <div>
                        <span className="text-muted-foreground uppercase font-semibold">Path</span>
                        <p className="text-sm font-mono mt-1 break-all">{selectedNode.data.path}</p>
                      </div>
                    )}

                    {/* Language & Size */}
                    {selectedNode.type === 'file' && (
                      <div className="grid grid-cols-2 gap-3">
                        <div className="bg-muted/30 rounded-lg p-2 text-center">
                          <p className="text-muted-foreground uppercase">Language</p>
                          <p className="font-bold mt-0.5 flex items-center justify-center gap-1">
                            {LANG_ICONS[selectedNode.data.language || ''] || <Code2 size={12} />}
                            {selectedNode.data.language || 'Unknown'}
                          </p>
                        </div>
                        <div className="bg-muted/30 rounded-lg p-2 text-center">
                          <p className="text-muted-foreground uppercase">Lines</p>
                          <p className="font-bold mt-0.5">{selectedNode.data.lines?.toLocaleString()}</p>
                        </div>
                      </div>
                    )}

                    {/* Imports */}
                    {selectedNode.data.imports && selectedNode.data.imports.length > 0 && (
                      <div>
                        <span className="text-muted-foreground uppercase font-semibold">
                          Imports ({selectedNode.data.imports.length})
                        </span>
                        <div className="mt-1 space-y-1">
                          {selectedNode.data.imports.slice(0, 15).map((imp, i) => (
                            <div key={i} className="flex items-center gap-1.5 text-blue-400">
                              <ArrowRight size={10} /> <span className="font-mono">{imp}</span>
                            </div>
                          ))}
                          {selectedNode.data.imports.length > 15 && (
                            <p className="text-muted-foreground">...and {selectedNode.data.imports.length - 15} more</p>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Routes */}
                    {selectedNode.data.routes && selectedNode.data.routes.length > 0 && (
                      <div>
                        <span className="text-muted-foreground uppercase font-semibold">
                          Routes ({selectedNode.data.routes.length})
                        </span>
                        <div className="mt-1 space-y-1">
                          {selectedNode.data.routes.map((r, i) => (
                            <div key={i} className="flex items-center gap-1.5">
                              <span className="px-1 py-0.5 rounded bg-emerald-500/10 text-emerald-400 text-[10px] font-bold">
                                {r.method || 'ANY'}
                              </span>
                              <span className="font-mono text-emerald-300">{r.path}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Models */}
                    {selectedNode.data.models && selectedNode.data.models.length > 0 && (
                      <div>
                        <span className="text-muted-foreground uppercase font-semibold">
                          Models ({selectedNode.data.models.length})
                        </span>
                        <div className="mt-1 space-y-1">
                          {selectedNode.data.models.map((m, i) => (
                            <div key={i} className="flex items-center gap-1.5 text-amber-400">
                              <Database size={10} /> <span className="font-mono">{m}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Route Detail */}
                    {selectedNode.type === 'route' && (
                      <div className="bg-emerald-500/5 rounded-lg p-3 border border-emerald-500/20">
                        <p className="text-emerald-400 font-bold">{selectedNode.data.label}</p>
                      </div>
                    )}

                    {/* Model Detail */}
                    {selectedNode.type === 'model' && (
                      <div className="bg-amber-500/5 rounded-lg p-3 border border-amber-500/20">
                        <p className="text-amber-400 font-bold">DB Model: {selectedNode.data.name}</p>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* AI Summary */}
          {result.summary && (
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="font-bold text-sm flex items-center gap-2 mb-3">
                <BarChart3 className="text-purple-500" size={16} /> AI Architecture Summary
              </h3>
              <p className="text-sm text-muted-foreground leading-relaxed whitespace-pre-wrap">
                {result.summary}
              </p>
            </div>
          )}

          {/* Language Breakdown */}
          <div className="bg-card border border-border rounded-xl p-5">
            <h3 className="font-bold text-sm mb-3">Language Breakdown</h3>
            <div className="space-y-2">
              {Object.entries(result.stats.languages)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 8)
                .map(([lang, lines]) => {
                  const pct = Math.round((lines / result.stats.lines) * 100);
                  return (
                    <div key={lang} className="flex items-center gap-3">
                      <span className="w-20 text-xs text-muted-foreground capitalize">{lang}</span>
                      <div className="flex-1 h-2 bg-muted/30 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: `${pct}%`,
                            backgroundColor: (NODE_CONFIG.file as any).baseColor,
                          }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground w-16 text-right">
                        {lines.toLocaleString()} ({pct}%)
                      </span>
                    </div>
                  );
                })}
            </div>
          </div>
        </motion.div>
      )}

      {/* Empty State */}
      {status === 'idle' && !result && (
        <div className="text-center py-16">
          <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center">
            <GitBranch className="text-purple-500" size={32} />
          </div>
          <h2 className="text-xl font-bold mb-2">Code Map</h2>
          <p className="text-muted-foreground mb-1 max-w-md mx-auto">
            Select a service and analyze its codebase to generate an interactive 3D map
            of files, imports, routes, and database models.
          </p>
          <p className="text-muted-foreground/60 text-xs max-w-sm mx-auto">
            The AI will clone the repo, walk the file tree, extract dependencies,
            and generate an architecture summary.
          </p>
        </div>
      )}
    </div>
  );
}
