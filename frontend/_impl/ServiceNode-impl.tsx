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
        <div className={`w-[220px] bg-zinc-900 border rounded-md shadow-sm transition-all hover:border-zinc-500 hover:shadow-md ${
            isSkipped ? 'border-zinc-800 opacity-60' : 'border-zinc-700'
        }`}>
            {/* Top Handle for incoming edges */}
            <Handle type="target" position={Position.Left} className="!bg-zinc-500" />

            <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950/50 px-2 py-1.5 rounded-t-md">
                <div className="flex items-center gap-1.5">
                    <GitBranch className="h-3 w-3 text-zinc-400" />
                    <span className="text-[10px] font-semibold text-zinc-200 truncate max-w-[100px]" title={svc.repo.split('/').pop()}>
                        {svc.repo.split('/').pop()}
                    </span>
                </div>
                <div className="flex items-center gap-2">
                    <div className="text-[9px] text-zinc-500 font-mono bg-zinc-900 border border-zinc-800 px-1.5 py-0.5 rounded">
                        #{svc.deploy_order}
                    </div>
                    <button
                        onClick={() => toggleSkip(idx)}
                        className={`text-[9px] px-1.5 py-0.5 rounded border transition-colors ${
                            isSkipped
                                ? 'border-zinc-800 text-zinc-500 hover:text-zinc-300'
                                : 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10'
                        }`}
                    >
                        {isSkipped ? 'Skipped' : 'Include'}
                    </button>
                </div>
            </div>

            <div className="p-2 space-y-2">
                <div className="flex flex-wrap gap-1">
                    {(svc.languages && svc.languages.length > 0 ? svc.languages : [svc.stack]).map((lang: string) => (
                        <span key={lang} className={`text-[9px] px-1.5 py-0.5 rounded border font-medium uppercase tracking-wider ${STACK_COLORS[lang.toLowerCase()] || STACK_COLORS.unknown}`}>
                            {lang}
                        </span>
                    ))}
                    <span className="text-[9px] text-zinc-500 border border-zinc-800 bg-zinc-950 rounded px-1.5 py-0.5">
                        :{svc.port}
                    </span>
                    {svc.addons?.map((a: string) => (
                        <span key={a} className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20 uppercase tracking-wider">
                            {a}
                        </span>
                    ))}
                </div>

                <div className="flex items-center justify-between gap-1.5 bg-zinc-950/50 p-1.5 rounded border border-zinc-800">
                    <Server size={10} className="text-zinc-500 shrink-0" />
                    <select
                        value={svc.server_id || 'local'}
                        onChange={(e) => updateServer(idx, e.target.value)}
                        disabled={isSkipped}
                        className="text-[9px] bg-transparent text-zinc-300 border-none rounded flex-1 outline-none transition-colors disabled:opacity-50 appearance-none"
                    >
                        <option value="local" className="bg-zinc-900 text-zinc-300">Local Server</option>
                        {servers?.map((s: any) => (
                            <option key={s.id} value={s.id} className="bg-zinc-900 text-zinc-300">{s.name} ({s.host})</option>
                        ))}
                    </select>
                </div>

                {Object.keys(svc.env_vars || {}).length > 0 && (
                    <div>
                        <button
                            onClick={() => setIsExpanded(!isExpanded)}
                            disabled={isSkipped}
                            className="text-[10px] text-blue-400 hover:text-blue-300 w-full text-left flex justify-between items-center"
                        >
                            <span>{Object.keys(svc.env_vars).length} Env Variables</span>
                            <span>{isExpanded ? '▲' : '▼'}</span>
                        </button>

                        {isExpanded && !isSkipped && (
                            <div className="mt-2 space-y-2 max-h-[120px] overflow-y-auto pr-1 nodrag custom-scrollbar">
                                <div className="flex justify-end mb-1">
                                    <button
                                        onClick={() => handlePasteEnv(idx)}
                                        className="text-[9px] text-blue-400 bg-blue-500/10 border border-blue-500/20 px-1.5 py-0.5 rounded hover:bg-blue-500/20 transition-colors"
                                    >
                                        Paste .env
                                    </button>
                                </div>
                                {Object.entries(svc.env_vars || {}).map(([key, value]: [string, any]) => (
                                    <div key={key} className="flex flex-col gap-1">
                                        <label className="text-[9px] font-mono text-zinc-500 truncate" title={key}>{key}</label>
                                        <input
                                            type="text"
                                            value={value}
                                            onChange={(e) => updateEnvVar(idx, key, e.target.value)}
                                            className="text-[10px] font-mono bg-zinc-950 text-zinc-300 border border-zinc-800 rounded px-2 py-1 w-full focus:border-blue-500 focus:outline-none"
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
            <Handle type="source" position={Position.Right} className="!bg-zinc-500" />
        </div>
    );
}
