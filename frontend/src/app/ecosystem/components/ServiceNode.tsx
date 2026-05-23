import React, { useState } from 'react';
import { Handle, Position } from 'reactflow';
import { GitBranch, Server, CheckCircle2, XCircle } from 'lucide-react';

const STACK_COLORS: Record<string, string> = {
    django: 'text-green-400 bg-green-500/10 border-green-500/20',
    python: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
    nextjs: 'text-white bg-zinc-500/10 border-zinc-500/20',
    node: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    rust: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
    go: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20',
    java: 'text-red-400 bg-red-500/10 border-red-500/20',
    ruby: 'text-red-400 bg-red-500/10 border-red-500/20',
    php: 'text-violet-400 bg-violet-500/10 border-violet-500/20',
    unknown: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20',
};

export function ServiceNode({ data }: any) {
    const { svc, idx, servers, updateServer, toggleSkip, updateEnvVar, handlePasteEnv } = data;
    const [isExpanded, setIsExpanded] = useState(false);

    const isSkipped = svc.skip;

    return (
        <div className={`w-[320px] bg-card border rounded-xl shadow-lg transition-all ${
            isSkipped ? 'border-border/50 opacity-60' : 'border-emerald-500/30 ring-1 ring-emerald-500/10'
        }`}>
            {/* Top Handle for incoming edges */}
            <Handle type="target" position={Position.Top} className="w-3 h-3 bg-muted-foreground border-2 border-background" />
            
            <div className="p-3 border-b border-border bg-muted/20 rounded-t-xl flex justify-between items-center">
                <div className="flex items-center gap-2">
                    <div className="text-xs text-muted-foreground font-mono bg-background border px-1.5 py-0.5 rounded">
                        #{svc.deploy_order}
                    </div>
                    <p className="font-bold text-sm truncate flex items-center gap-1.5 max-w-[180px]">
                        <GitBranch size={12} className="text-muted-foreground" />
                        {svc.repo.split('/').pop()}
                    </p>
                </div>
                <button
                    onClick={() => toggleSkip(idx)}
                    className={`text-[10px] px-2 py-1 rounded border transition-colors ${
                        isSkipped
                            ? 'border-border text-muted-foreground hover:text-foreground'
                            : 'border-emerald-500/30 text-emerald-500 bg-emerald-500/10'
                    }`}
                >
                    {isSkipped ? 'Skipped' : 'Include'}
                </button>
            </div>

            <div className="p-3 space-y-3">
                <div className="flex flex-wrap gap-1.5">
                    {(svc.languages && svc.languages.length > 0 ? svc.languages : [svc.stack]).map((lang: string) => (
                        <span key={lang} className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${STACK_COLORS[lang.toLowerCase()] || STACK_COLORS.unknown}`}>
                            {lang}
                        </span>
                    ))}
                    <span className="text-[10px] text-muted-foreground border border-border rounded px-1.5 py-0.5">
                        :{svc.port}
                    </span>
                    {svc.addons?.map((a: string) => (
                        <span key={a} className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">
                            {a}
                        </span>
                    ))}
                </div>

                <div className="flex items-center justify-between gap-2 bg-muted/30 p-2 rounded-lg border border-border">
                    <Server size={12} className="text-muted-foreground shrink-0" />
                    <select
                        value={svc.server_id || 'local'}
                        onChange={(e) => updateServer(idx, e.target.value)}
                        disabled={isSkipped}
                        className="text-[10px] bg-background border border-border rounded px-2 py-1 flex-1 outline-none focus:border-primary transition-colors disabled:opacity-50"
                    >
                        <option value="local">Local Server</option>
                        {servers?.map((s: any) => (
                            <option key={s.id} value={s.id}>{s.name} ({s.host})</option>
                        ))}
                    </select>
                </div>

                {Object.keys(svc.env_vars || {}).length > 0 && (
                    <div>
                        <button
                            onClick={() => setIsExpanded(!isExpanded)}
                            disabled={isSkipped}
                            className="text-[10px] text-primary hover:underline w-full text-left flex justify-between items-center"
                        >
                            <span>{Object.keys(svc.env_vars).length} Environment Variables</span>
                            <span>{isExpanded ? '▲' : '▼'}</span>
                        </button>
                        
                        {isExpanded && !isSkipped && (
                            <div className="mt-2 space-y-2 max-h-[150px] overflow-y-auto pr-1 nodrag">
                                <div className="flex justify-end mb-1">
                                    <button
                                        onClick={() => handlePasteEnv(idx)}
                                        className="text-[9px] text-primary bg-primary/10 px-1.5 py-0.5 rounded hover:bg-primary/20 transition-colors"
                                    >
                                        Paste .env
                                    </button>
                                </div>
                                {Object.entries(svc.env_vars || {}).map(([key, value]: [string, any]) => (
                                    <div key={key} className="flex flex-col gap-1">
                                        <label className="text-[9px] font-mono text-muted-foreground truncate">{key}</label>
                                        <input
                                            type="text"
                                            value={value}
                                            onChange={(e) => updateEnvVar(idx, key, e.target.value)}
                                            className="text-[10px] font-mono bg-background border border-border rounded px-2 py-1 w-full"
                                            placeholder="Empty value"
                                        />
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Bottom Handle for outgoing edges */}
            <Handle type="source" position={Position.Bottom} className="w-3 h-3 bg-primary border-2 border-background" />
        </div>
    );
}
